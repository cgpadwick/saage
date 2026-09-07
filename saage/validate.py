"""Structural validation of a flow.yaml spec.

`build_flow` used to index the parsed dict directly, so authoring mistakes
surfaced as bare KeyErrors (`KeyError: 'provider'`) or, worse, misleading ones
(a step missing `id` raised a KeyError naming the *skill*). `validate_spec`
walks the spec first and reports every problem in one pass, addressed by step
position and id.
"""
from __future__ import annotations

import re

STEP_TYPES = ("agent", "command", "retry_loop", "polling_loop", "counting_loop")

_MCP_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_MCP_KEYS = {"command", "args", "env"}


class FlowSpecError(ValueError):
    """flow.yaml is malformed; the message lists every problem found."""


def _where(path: str, spec: dict) -> str:
    sid = spec.get("id")
    return f"{path} (id {sid!r})" if sid else path


def _check_step(spec, path: str, errors: list[str]) -> None:
    if not isinstance(spec, dict):
        errors.append(f"{path}: a step must be a mapping, got {type(spec).__name__}")
        return
    w = _where(path, spec)
    t = spec.get("type")
    if t is None:
        errors.append(f"{w}: missing 'type' (one of: {', '.join(STEP_TYPES)})")
        return
    if t not in STEP_TYPES:
        errors.append(f"{w}: unknown step type {t!r} (one of: {', '.join(STEP_TYPES)})")
        return
    if not spec.get("id"):
        errors.append(f"{path}: {t} step needs an 'id'")
    if t == "agent" and not spec.get("skill"):
        errors.append(f"{w}: agent step needs 'skill' (a skill directory name)")
    if t == "command" and not spec.get("run"):
        errors.append(f"{w}: command step needs 'run' (the shell command)")
    if t == "retry_loop":
        for k in ("action", "check"):
            if k not in spec:
                errors.append(f"{w}: retry_loop needs '{k}' (a nested step)")
            else:
                _check_step(spec[k], f"{w}.{k}", errors)
    if t == "polling_loop":
        for k in ("interval_seconds", "max_wait_seconds"):
            if k not in spec:
                errors.append(f"{w}: polling_loop needs '{k}'")
        for k in ("poll", "status"):
            if k not in spec:
                errors.append(f"{w}: polling_loop needs '{k}' (a nested step)")
            else:
                _check_step(spec[k], f"{w}.{k}", errors)
    if t == "counting_loop":
        body = spec.get("body")
        if not isinstance(body, list) or not body:
            errors.append(f"{w}: counting_loop needs a non-empty 'body' list of steps")
        else:
            for i, s in enumerate(body):
                _check_step(s, f"{w}.body[{i}]", errors)


def _check_mcp_server(name, entry, errors: list[str]) -> None:
    w = f"mcp.{name}"
    if not isinstance(name, str) or not _MCP_NAME.match(str(name)):
        errors.append(f"{w}: server name must match [A-Za-z0-9_-]+ "
                      f"(it prefixes tool names)")
    if not isinstance(entry, dict):
        errors.append(f"{w}: must be a mapping with 'command' (and optional "
                      f"'args', 'env')")
        return
    for k in entry:
        if k not in _MCP_KEYS:
            errors.append(f"{w}: unknown key {k!r} (allowed: command, args, env)")
    if not entry.get("command") or not isinstance(entry.get("command"), str):
        errors.append(f"{w}: needs 'command' (the executable, e.g. uvx)")
    args = entry.get("args")
    if args is not None and (not isinstance(args, list)
                             or not all(isinstance(a, str) for a in args)):
        errors.append(f"{w}: 'args' must be a list of strings")
    env = entry.get("env")
    if env is not None and (not isinstance(env, list)
                            or not all(isinstance(e, str) for e in env)):
        errors.append(f"{w}: 'env' must be a list of env-var NAMES (values "
                      f"come from the environment or `saage mcp add`)")


def validate_spec(spec, require_provider: bool = True) -> None:
    """Raise FlowSpecError listing every structural problem in *spec*.

    `require_provider=False` skips the provider block (used when a ready
    provider object is injected, e.g. hydrate-only checks and tests).
    """
    if not isinstance(spec, dict):
        got = "an empty file" if spec is None else type(spec).__name__
        raise FlowSpecError(f"flow.yaml must be a YAML mapping, got {got}")
    errors: list[str] = []
    if require_provider:
        # `provider:` is optional — an absent block falls back to the user's
        # `saage setup` defaults at build time. When present it pins the flow
        # to a provider, so it must be complete (models are provider-specific).
        prov = spec.get("provider")
        if prov is not None and not isinstance(prov, dict):
            errors.append("'provider:' must be a mapping with 'type' and 'model'")
        elif prov is not None:
            if not prov.get("type"):
                errors.append("provider: missing 'type' (anthropic | openai | "
                              "openrouter | nvidia | local)")
            if not prov.get("model"):
                errors.append("provider: missing 'model'")
    mcp = spec.get("mcp")
    if mcp is not None and not isinstance(mcp, dict):
        errors.append("'mcp:' must be a mapping of server-name -> "
                      "{command, args, env}")
    elif mcp:
        for name, entry in mcp.items():
            _check_mcp_server(name, entry, errors)
    wf = spec.get("workflow")
    if wf is None:
        errors.append("missing top-level 'workflow:' list of steps")
    elif not isinstance(wf, list) or not wf:
        errors.append("'workflow:' must be a non-empty list of steps")
    else:
        for i, s in enumerate(wf):
            _check_step(s, f"workflow[{i}]", errors)
    if errors:
        raise FlowSpecError("invalid flow spec:\n  - " + "\n  - ".join(errors))
