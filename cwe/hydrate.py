"""Hydrate a YAML workflow spec into a runnable PocketFlow flow.

`build_step` recursively maps each YAML step `type` to a node or a primitive
sub-flow; top-level steps are chained with PocketFlow's `>>`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import EngineConfig, load_engine_config
from .llm import AnthropicProvider, OpenAIProvider
from .nodes import AgentNode, CommandNode
from .primitives import Subflow, counting_loop, polling_loop, retry_loop
from .skills import Skill, load_skills
from .tools import default_tools

log = logging.getLogger(__name__)


@dataclass
class Context:
    root: Path
    provider: object
    skills: dict[str, Skill]
    tools: list


def make_provider(spec: dict):
    """Out of the box: anthropic | openai | openrouter | local."""
    t = spec["type"]
    model = spec["model"]
    if t == "anthropic":
        return AnthropicProvider(model)
    if t == "openai":
        return OpenAIProvider(model)
    if t == "openrouter":
        return OpenAIProvider(model, base_url="https://openrouter.ai/api/v1",
                              api_key_env="OPENROUTER_API_KEY")
    if t == "local":
        return OpenAIProvider(
            model,
            base_url=spec.get("base_url", "http://localhost:11434/v1"),
            api_key_env=spec.get("api_key_env", "LOCAL_API_KEY"))
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
                           captures=spec.get("set"))
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


def build_flow(flow_yaml, provider=None, provider_overrides: dict | None = None,
               config: "str | Path | EngineConfig | None" = None):
    """Return (flow, shared).

    `provider` injects a ready provider object (used by tests). Otherwise the
    YAML `provider` block is used, with any `provider_overrides` (e.g. {"type":
    "openrouter", "model": "..."}) merged on top — handy for switching provider
    or model from the CLI without editing the flow.

    `config` is the engine config governing the `run_command` safety policy:
    an `EngineConfig`, a path to an engine YAML, or None for the safe built-in
    defaults. The denylist is always applied to flows built here, so the default
    execution path is restricted out of the box.
    """
    flow_yaml = Path(flow_yaml)
    log.info("loading flow: %s", flow_yaml)
    spec = yaml.safe_load(flow_yaml.read_text())
    root = flow_yaml.parent
    cfg = config if isinstance(config, EngineConfig) else load_engine_config(config)
    if provider is None:
        pspec = dict(spec["provider"])
        for k, v in (provider_overrides or {}).items():
            if v is not None:
                pspec[k] = v
        provider = make_provider(pspec)
        log.info("provider: %s / %s", pspec.get("type"), pspec.get("model"))
    skills = load_skills(root)
    log.info("loaded %d skill(s): %s", len(skills), ", ".join(skills) or "(none)")
    ctx = Context(root=root, provider=provider, skills=skills,
                  tools=default_tools(root, command_policy=cfg.command_policy))
    steps = [build_step(s, ctx) for s in spec["workflow"]]
    for a, b in zip(steps, steps[1:]):
        a >> b
    log.info("workflow ready: %d top-level step(s)", len(steps))
    return Subflow(start=steps[0]), dict(spec.get("shared", {}))


def run_flow(flow_yaml, provider=None, shared: dict | None = None,
             provider_overrides: dict | None = None,
             config: "str | Path | EngineConfig | None" = None) -> dict:
    flow, seed = build_flow(flow_yaml, provider=provider,
                            provider_overrides=provider_overrides, config=config)
    if shared:
        seed.update(shared)
    log.info("starting run%s", f" (seed: {seed})" if seed else "")
    flow.run(seed)
    log.info("run complete")
    return seed
