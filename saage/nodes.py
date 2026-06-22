"""PocketFlow Node subclasses. All cross-iteration state lives in `shared`
(PocketFlow shallow-copies nodes each step, so node-local counters would be lost).
"""
from __future__ import annotations

import logging
import re
import time

from jinja2 import Environment, Undefined, make_logging_undefined
from pocketflow import Node

from .agent import run_agent
from .shell import run_shell
from .skills import Skill
from .tools import Tool, venv_env

log = logging.getLogger(__name__)


# Render templates so an undefined `{{ var }}` logs a warning instead of silently
# becoming "" — a missing shared value / typo is then visible in the run log.
_LoggingUndefined = make_logging_undefined(logger=log, base=Undefined)
_jinja = Environment(undefined=_LoggingUndefined)


def render(template: str, shared: dict) -> str:
    """`{{ var }}` substitution from the shared store, used for every text the
    engine feeds a step: a `command:` run string, and an agent's skill
    *description* AND *body*. Undefined names still render to "" (as Jinja does)
    but emit a warning. To keep a literal brace from being interpreted, wrap it in
    `{% raw %}…{% endraw %}`."""
    return _jinja.from_string(template).render(**shared)


def capture_into(shared: dict, text: str, captures: dict | None) -> None:
    """Pull values out of node output into top-level shared keys so that
    exit_when (`accuracy >= target`) and templates (`{{ job_id }}`) can use them.

    YAML: `set: { accuracy: 'ACCURACY=([0-9.]+)', job_id: 'job (\\d+)' }`
    """
    for key, pattern in (captures or {}).items():
        last = None
        for m in re.finditer(pattern, text):    # take the last match, not the first
            last = m
        if last is None:
            continue
        v = last.group(1) if last.groups() else last.group(0)
        try:                                    # numeric coercion for predicates
            v = float(v) if "." in v else int(v)
        except ValueError:
            pass
        shared[key] = v


def _trace(shared: dict, node_id: str) -> None:
    shared.setdefault("_trace", []).append(node_id)


def _parse_action(text: str) -> str:
    """Return the action from the last `ACTION: <x>` found anywhere in the text
    (the skill convention is to end the reply with one), else 'default'. Tolerant
    of markdown/punctuation around it, e.g. `**ACTION: pass**` -> `pass`."""
    action = "default"
    for m in re.finditer(r"ACTION:\W*([A-Za-z_][\w-]*)", text, flags=re.IGNORECASE):
        action = m.group(1)
    return action


def _route(node: "Node", action: str) -> str:
    """Map a node's parsed action to one its wiring actually has a successor for.

    An ACTION is only a routing signal where the graph offers that route. An
    action-role node (a retry_loop action, a counting_loop body step, a poll
    step) has a single `default` successor, so a stray `ACTION:` in its
    free-text — e.g. an `implement` agent narrating "...run `ACTION: python`
    predict.py..." — must NOT be taken as a route. If the parsed action has no
    matching successor but a `default` edge exists, fall through to `default`
    rather than letting PocketFlow silently END the whole flow on an unknown
    action. Decider nodes (checks/classifiers) have named successors and no
    `default`, so their routing is unchanged.
    """
    succ = getattr(node, "successors", {}) or {}
    if action in succ or "default" not in succ:
        return action
    if action != "default":
        log.warning("  %s: action %r has no transition; routing via 'default'",
                    getattr(node, "id", "?"), action)
    return "default"


class AgentNode(Node):
    """Runs an LLM agent (a skill) with the harness tools."""

    def __init__(self, id: str, skill: Skill, provider, tools: list[Tool],
                 captures: dict | None = None, max_steps: int = 20):
        super().__init__()
        self.id = id
        self.skill = skill
        self.provider = provider
        self.captures = captures
        self.max_steps = max_steps
        allow = set(skill.tools) if skill.tools else None
        self.tools = [t for t in tools if allow is None or t.name in allow]
        if allow is not None:
            # a tools: allow-list with names that don't exist is a config error —
            # warn (so a typo is visible) and hard-fail if it leaves the agent with
            # no tools, rather than silently running tool-less.
            available = {t.name for t in tools}
            unknown = allow - available
            if unknown:
                log.warning("skill %r lists unknown tool(s) in tools: %s "
                            "(available: %s)", skill.name, ", ".join(sorted(unknown)),
                            ", ".join(sorted(available)) or "(none)")
            if not self.tools:
                raise ValueError(
                    f"skill {skill.name!r} tools: lists only unknown tool(s) "
                    f"{sorted(allow)}; none match the available tools "
                    f"{sorted(available)} — fix the names in the skill's "
                    f"frontmatter")

    def prep(self, shared):
        task = render(self.skill.description or self.skill.name, shared)
        feedback = shared.get("_feedback", {}).get(self.id)
        if feedback:
            task = f"{task}\n\n--- Feedback from previous attempt ---\n{feedback}"
        system = render(self.skill.system, shared)   # body is templated too
        return {"task": task, "system": system}

    def exec(self, prep_res):
        log.info("▶ %s  [agent: %s]", self.id, self.skill.name)
        return run_agent(self.provider, prep_res["system"], prep_res["task"],
                         self.tools, self.max_steps)

    def post(self, shared, prep_res, out):
        shared.setdefault("results", {})[self.id] = out
        _trace(shared, self.id)
        capture_into(shared, out, self.captures)
        action = _route(self, _parse_action(out))
        log.info("  ✓ %s → %s", self.id, action)
        return action


class CommandNode(Node):
    """Deterministic shell step (no LLM)."""

    def __init__(self, id: str, command: str, root, captures: dict | None = None,
                 venv: str | None = None):
        super().__init__()
        self.id = id
        self.command = command
        self.root = root
        self.captures = captures
        self.venv = venv

    def prep(self, shared):
        return render(self.command, shared)

    def exec(self, cmd):
        log.info("$ %s", cmd)
        r = run_shell(cmd, cwd=self.root, env=venv_env(self.root, self.venv))
        log.info("  ✓ %s → exit=%d", self.id, r.returncode)
        return {"exit": r.returncode, "stdout": r.stdout, "stderr": r.stderr}

    def post(self, shared, prep_res, out):
        shared.setdefault("results", {})[self.id] = out
        _trace(shared, self.id)
        capture_into(shared, out["stdout"], self.captures)
        # commands can drive loop checks deterministically by printing
        # `ACTION: pass|fail|...` (same convention as agent skills); without
        # one, behavior is unchanged ("default")
        return _route(self, _parse_action(out["stdout"]))


class WaitNode(Node):
    """Sleeps between polls (interval_seconds)."""

    def __init__(self, seconds: float):
        super().__init__()
        self.seconds = seconds

    def exec(self, _):
        if self.seconds:
            log.info("  ⏲ waiting %ss before next poll", self.seconds)
            time.sleep(self.seconds)


class LoopGuard(Node):
    """retry_loop counter. On each failure, re-injects the checker's feedback
    into the action node so the next attempt can address it."""

    def __init__(self, name: str, max_iters: int,
                 action_id: str | None, check_id: str | None):
        super().__init__()
        self.name = name
        self.max_iters = max_iters
        self.action_id = action_id
        self.check_id = check_id

    def post(self, shared, prep_res, out):
        counts = shared.setdefault("_iter", {})
        counts[self.name] = counts.get(self.name, 0) + 1
        if self.action_id and self.check_id:
            feedback = shared.get("results", {}).get(self.check_id)
            if feedback is not None:
                shared.setdefault("_feedback", {})[self.action_id] = feedback
        if counts[self.name] >= self.max_iters:
            log.info("↻ %s: %d/%d attempts failed — giving up",
                     self.name, counts[self.name], self.max_iters)
            return "stop"
        log.info("↻ %s: attempt %d failed — retrying with feedback",
                 self.name, counts[self.name])
        return "again"


class TimeoutGuard(Node):
    """polling_loop wall-clock cap. Returns 'stop' once max_wait elapses."""

    def __init__(self, name: str, max_wait: float):
        super().__init__()
        self.name = name
        self.max_wait = max_wait

    def post(self, shared, prep_res, out):
        starts = shared.setdefault("_poll_start", {})
        now = time.monotonic()
        starts.setdefault(self.name, now)
        counts = shared.setdefault("_poll_count", {})
        counts[self.name] = counts.get(self.name, 0) + 1
        elapsed = now - starts[self.name]
        if elapsed >= self.max_wait:
            log.info("↻ %s: still running after %.0fs — max_wait (%ss) reached, "
                     "giving up", self.name, elapsed, self.max_wait)
            return "stop"
        log.debug("%s: poll %d, %.0fs elapsed", self.name, counts[self.name], elapsed)
        return "again"


class GateNode(Node):
    """counting_loop counter + optional exit predicate over the shared store."""

    def __init__(self, name: str, max_iters: int, exit_when: str | None):
        super().__init__()
        self.name = name
        self.max_iters = max_iters
        self.exit_when = exit_when

    def post(self, shared, prep_res, out):
        counts = shared.setdefault("_iter", {})
        counts[self.name] = counts.get(self.name, 0) + 1
        n = counts[self.name]
        if n >= self.max_iters:
            shared.setdefault("_exit_reason", {})[self.name] = "max_iterations"
            log.info("✓ %s: reached max_iterations (%d) — exiting loop",
                     self.name, self.max_iters)
            return "exit"
        if self.exit_when and _safe_eval(self.exit_when, shared):
            shared.setdefault("_exit_reason", {})[self.name] = "exit_when"
            log.info("✓ %s: exit_when satisfied (%s) after %d iteration(s)",
                     self.name, self.exit_when, n)
            return "exit"
        log.info("↻ %s: iteration %d/%d done — continuing", self.name, n, self.max_iters)
        return "continue"


def _safe_eval(expr: str, shared: dict) -> bool:
    """Evaluate an exit predicate against shared with no builtins available.

    A name the predicate references but that no step has populated yet (e.g. a
    metric captured only after the first iteration) evaluates to False — the loop
    keeps going instead of crashing — and we warn, so a genuine typo in
    `exit_when` is still visible in the logs rather than silently never firing.
    """
    try:
        return bool(eval(expr, {"__builtins__": {}}, dict(shared)))
    except NameError as e:
        log.warning("exit_when %r references an undefined name (%s); "
                    "treating as not-yet-satisfied — seed it in `shared:` if "
                    "this is not a typo", expr, e)
        return False
