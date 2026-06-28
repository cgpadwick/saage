"""Hydrate a YAML workflow spec into a runnable PocketFlow flow.

`build_step` recursively maps each YAML step `type` to a node or a primitive
sub-flow; top-level steps are chained with PocketFlow's `>>`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import EngineConfig, load_engine_config
from .llm import AnthropicProvider, OpenAIProvider
from .nodes import AgentNode, CommandNode
from .retry import RetryPolicy
from .primitives import Subflow, counting_loop, polling_loop, retry_loop
from .skills import Skill, load_skills
from .tools import default_tools

log = logging.getLogger(__name__)


@dataclass
class Context:
    root: Path                # the workspace: tool sandbox + command cwd
    provider: object
    skills: dict[str, Skill]
    tools: list
    venv: str | None = None   # venv to auto-activate for commands (relative to root)


def make_provider(spec: dict):
    """Out of the box: anthropic | openai | openrouter | nvidia | local.

    An optional `retry:` sub-block tunes the transient-failure backoff, e.g.
    `provider: { type: anthropic, model: ..., retry: { max_attempts: 8 } }`.
    """
    t = spec["type"]
    model = spec["model"]
    rp = RetryPolicy(**spec["retry"]) if spec.get("retry") else None
    if t == "anthropic":
        return AnthropicProvider(model, retry_policy=rp)
    if t == "openai":
        return OpenAIProvider(model, retry_policy=rp)
    if t == "openrouter":
        return OpenAIProvider(model, base_url="https://openrouter.ai/api/v1",
                              api_key_env="OPENROUTER_API_KEY", retry_policy=rp)
    if t == "nvidia":
        return OpenAIProvider(model, base_url="https://integrate.api.nvidia.com/v1",
                              api_key_env="NVIDIA_API_KEY", retry_policy=rp)
    if t == "local":
        return OpenAIProvider(
            model,
            base_url=spec.get("base_url", "http://localhost:11434/v1"),
            api_key_env=spec.get("api_key_env", "LOCAL_API_KEY"), retry_policy=rp)
    raise ValueError(f"unknown provider type: {t!r}")


def build_step(spec: dict, ctx: Context):
    t = spec["type"]
    log.debug("building step %s [%s]", spec.get("id", "?"), t)
    if t == "agent":
        skill = ctx.skills[spec["skill"]]
        return AgentNode(spec["id"], skill, ctx.provider, ctx.tools,
                         captures=spec.get("set"),
                         max_steps=spec.get("max_steps", 20))
    if t == "command":
        return CommandNode(spec["id"], spec["run"], ctx.root,
                           captures=spec.get("set"), venv=ctx.venv)
    if t == "retry_loop":
        return retry_loop(spec["id"],
                          build_step(spec["action"], ctx),
                          build_step(spec["check"], ctx),
                          spec.get("max_iterations", 3))
    if t == "polling_loop":
        return polling_loop(spec["id"],
                            build_step(spec["poll"], ctx),
                            build_step(spec["status"], ctx),
                            spec["interval_seconds"],
                            spec["max_wait_seconds"])
    if t == "counting_loop":
        body = [build_step(s, ctx) for s in spec["body"]]
        return counting_loop(spec["id"], body,
                             spec.get("max_iterations", 10),
                             spec.get("exit_when"))
    raise ValueError(f"unknown step type: {t!r}")


def _tag_step(node, idx: int, seen=None) -> None:
    """Set `_step_index = idx` on every node reachable from a top-level step
    (the step itself, a loop subflow, and all body/guard nodes). Must run BEFORE
    top-level steps are chained, so the walk does not cross into later steps."""
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    node._step_index = idx
    start = getattr(node, "start_node", None)
    if start is not None:
        _tag_step(start, idx, seen)
    for nxt in getattr(node, "successors", {}).values():
        _tag_step(nxt, idx, seen)


def _all_subflows(node, seen=None, out=None):
    seen = set() if seen is None else seen
    out = [] if out is None else out
    if node is None or id(node) in seen:
        return out
    seen.add(id(node))
    if isinstance(node, Subflow):
        out.append(node)
    start = getattr(node, "start_node", None)
    if start is not None:
        _all_subflows(start, seen, out)
    for nxt in getattr(node, "successors", {}).values():
        _all_subflows(nxt, seen, out)
    return out


def build_flow(flow_yaml, provider=None, provider_overrides: dict | None = None,
               workspace=None, venv: str | None = None,
               config: "str | Path | EngineConfig | None" = None,
               checkpoint=None, resume_step: int | None = None):
    """Return (flow, shared).

    `provider` injects a ready provider object (used by tests). Otherwise the
    YAML `provider` block is used, with any `provider_overrides` (e.g. {"type":
    "openrouter", "model": "..."}) merged on top — handy for switching provider
    or model from the CLI without editing the flow.

    `workspace` is the tool sandbox + command working dir. Resolution order:
    arg → `workspace:` in the YAML → the flow file's directory (back-compat).
    Skills are always loaded from the flow file's directory. `venv` (arg →
    `venv:` in YAML → default ".venv") is auto-activated for commands once it
    exists on disk.

    `config` is the engine config governing the `run_command` safety policy: an
    `EngineConfig`, a path to an engine YAML, or None for the safe built-in
    denylist (always applied, so the default execution path is restricted).
    """
    flow_yaml = Path(flow_yaml)
    log.info("loading flow: %s", flow_yaml)
    spec = yaml.safe_load(flow_yaml.read_text(encoding="utf-8"))
    flow_dir = flow_yaml.parent
    cfg = config if isinstance(config, EngineConfig) else load_engine_config(config)
    # resolve so {{ workspace }} is always an absolute, canonical path (matching
    # flow_dir below) — a relative --workspace otherwise leaks into prompts/commands.
    ws = Path(workspace or spec.get("workspace") or flow_dir).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)
    venv = venv or spec.get("venv") or ".venv"
    if ws != flow_dir.resolve():
        log.info("workspace: %s", ws)
    if provider is None:
        pspec = dict(spec["provider"])
        for k, v in (provider_overrides or {}).items():
            if v is not None:
                pspec[k] = v
        provider = make_provider(pspec)
        log.info("provider: %s / %s", pspec.get("type"), pspec.get("model"))
    skills = load_skills(flow_dir)
    log.info("loaded %d skill(s): %s", len(skills), ", ".join(skills) or "(none)")
    ctx = Context(root=ws, provider=provider, skills=skills,
                  tools=default_tools(ws, venv=venv, command_policy=cfg.command_policy),
                  venv=venv)
    steps = [build_step(s, ctx) for s in spec["workflow"]]
    for k, step in enumerate(steps):
        _tag_step(step, k)               # tag BEFORE chaining (walk stays in-step)
    for a, b in zip(steps, steps[1:]):
        a >> b
    log.info("workflow ready: %d top-level step(s)", len(steps))
    top = Subflow(start=steps[0])
    if checkpoint is not None:
        for sf in _all_subflows(top):    # top + every nested loop subflow
            sf.sink = checkpoint
    if resume_step is not None:
        top.start_node = steps[resume_step]
        if isinstance(steps[resume_step], Subflow):
            # the resumed loop must keep its restored _iter counter on first entry
            steps[resume_step]._skip_reset_once = True
    seed = dict(spec.get("shared", {}))
    seed.setdefault("workspace", str(ws))
    seed.setdefault("venv", venv)
    seed.setdefault("flow_dir", str(flow_dir.resolve()))   # for bundled scripts
    # the interpreter launcher for helper scripts: Windows has no `python3`
    seed.setdefault("python", "python" if os.name == "nt" else "python3")
    return top, seed


def run_flow(flow_yaml, provider=None, shared: dict | None = None,
             provider_overrides: dict | None = None,
             workspace=None, venv: str | None = None,
             config: "str | Path | EngineConfig | None" = None,
             checkpoint=None, resume=None) -> dict:
    resume_step = None
    if resume is not None:
        rec = resume.load()
        resume_step = rec["resume_step"]
        checkpoint = checkpoint or resume          # write back into the same run
    flow, seed = build_flow(flow_yaml, provider=provider,
                            provider_overrides=provider_overrides,
                            workspace=workspace, venv=venv, config=config,
                            checkpoint=checkpoint, resume_step=resume_step)
    if resume is not None:
        # build_flow just seeded the resume-time workspace/venv/flow_dir/python;
        # keep them over the restored store so {{ workspace }} matches the real
        # execution dir even when resuming with a different --workspace.
        fresh_paths = {k: seed[k] for k in ("workspace", "venv", "flow_dir", "python")
                       if k in seed}
        seed = dict(rec["shared"])                 # restore the whole store
        seed.update(fresh_paths)
        seed.pop("_poll_start", None)              # monotonic clocks from the
        seed.pop("_poll_count", None)              # dead process are meaningless
        log.info("resuming run at step %s", resume_step)
    if shared:
        seed.update(shared)
    log.info("starting run%s", f" (seed: {seed})" if seed and resume is None else "")
    # The engine stamps the terminal completed/failed status into the final
    # checkpoint write; here we only need to record a crash (a node that raised
    # before that final write could happen).
    try:
        flow.run(seed)
    except BaseException:
        if checkpoint is not None:
            checkpoint.mark("failed")
        raise
    log.info("run complete")
    return seed
