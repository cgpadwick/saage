"""Host mode: the calling coding agent IS the LLM.

`provider: { type: host }` (or `--provider host`) runs a flow with no API key.
The engine still owns all control flow — ordering, loop bounds, exit_when,
command steps — but each agent step is handed, whole, to whatever agent is
driving the run (Claude Code, Cursor, …) through a file mailbox in the run
dir. The host does the step with its own model and tools and hands back the
final text; captures/routing then proceed exactly as for any provider.

Mailbox protocol (`<run_dir>/host/`), one step outstanding at a time:
    engine → pending.json        {seq, step_id, skill, system, task, tools, workspace}
    host   → result-<seq>.json   {seq, output}
Both writes are atomic (tmp + rename), so neither side ever reads a torn file.
The engine's wait is bounded by `timeout` (a vanished host must not hang a run
forever); the MCP server (`next_task` / `submit_result`) is the usual host side.

Host mode is local-only: the host's tools act on the host's filesystem, so a
remote handoff (whose workspace lives on another box) refuses it.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .llm import LLMResponse

log = logging.getLogger(__name__)

MAILBOX_ENV = "SAAGE_HOST_MAILBOX"   # set by `saage run` to <run_dir>/host
DEFAULT_TIMEOUT = 3600.0             # max seconds to wait for one step's result
_POLL = 0.2


class HostTimeout(RuntimeError):
    """The host never answered a posted step within the timeout."""


def _write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def read_pending(mailbox: "str | Path") -> dict | None:
    """The outstanding step, or None if the engine isn't waiting on one.
    A step already answered (its result written, the engine not yet woken to
    clear pending.json) is NOT outstanding — so it is never handed out twice."""
    p = Path(mailbox) / "pending.json"
    try:
        pending = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if (Path(mailbox) / f"result-{pending['seq']}.json").exists():
        return None
    return pending


def submit(mailbox: "str | Path", seq: int, output: str) -> None:
    """Answer step `seq`. Raises ValueError if it isn't the outstanding one —
    a stale or duplicate submit must not be applied to a later step."""
    pending = read_pending(mailbox)
    if pending is None or pending["seq"] != seq:
        have = "none" if pending is None else pending["seq"]
        raise ValueError(f"step {seq} is not pending (outstanding: {have})")
    _write_json(Path(mailbox) / f"result-{seq}.json", {"seq": seq, "output": output})


def wait_next(mailbox: "str | Path", status, timeout: float) -> tuple[str, object]:
    """Block until the run needs its next agent step — the one wait loop both
    hosts (MCP next_task, `saage host next`) share. `status()` gives the job's
    status. Returns ("task", pending) | ("done", status) | ("waiting", status)
    after `timeout` while the engine is busy (e.g. a long command step)."""
    deadline = time.monotonic() + timeout
    while True:
        pending = read_pending(mailbox)
        if pending is not None:
            return "task", pending
        st = status()
        if st != "running":
            return "done", st
        if time.monotonic() >= deadline:
            return "waiting", st
        time.sleep(_POLL)


class HostProvider:
    """Delegates each agent step to the host agent via the mailbox.

    `delegates_steps` tells AgentNode to call run_step() with the whole step
    instead of driving the per-turn tool loop: the host runs its own loop with
    its own tools, so saage only ever sees the step's final answer."""

    delegates_steps = True

    def __init__(self, mailbox: "str | Path", timeout: float = DEFAULT_TIMEOUT):
        self.mailbox = Path(mailbox)
        self.timeout = float(timeout)
        self.mailbox.mkdir(parents=True, exist_ok=True)
        # resume-safe numbering: continue past any results already on disk
        done = [int(p.stem.split("-", 1)[1]) for p in self.mailbox.glob("result-*.json")]
        self._seq = max(done, default=0)

    def run_step(self, step_id: str, skill: str, system: str, task: str,
                 tools: list[str], workspace: str | None = None) -> str:
        self._seq += 1
        seq = self._seq
        result = self.mailbox / f"result-{seq}.json"
        _write_json(self.mailbox / "pending.json", {
            "seq": seq, "step_id": step_id, "skill": skill,
            "system": system, "task": task, "tools": tools,
            # relative paths in the task ("read history.txt") mean files here
            "workspace": workspace})
        log.info("    ⇄ waiting for host agent (step %d)", seq)
        deadline = time.monotonic() + self.timeout
        while not result.exists():
            if time.monotonic() >= deadline:
                (self.mailbox / "pending.json").unlink(missing_ok=True)
                raise HostTimeout(
                    f"host agent did not answer step {step_id!r} within "
                    f"{self.timeout:.0f}s")
            time.sleep(_POLL)
        out = json.loads(result.read_text(encoding="utf-8"))["output"]
        (self.mailbox / "pending.json").unlink(missing_ok=True)
        return out

    def complete(self, system, messages, tools):
        # only reached if something drives the per-turn loop directly; treat
        # it as one delegated step whose answer is final (no tool calls)
        return LLMResponse(text=self.run_step(
            "?", "?", system, messages[0]["text"], [t.name for t in tools]))
