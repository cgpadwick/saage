"""Structural validation of a flow.yaml spec.

`build_flow` used to index the parsed dict directly, so authoring mistakes
surfaced as bare KeyErrors (`KeyError: 'provider'`) or, worse, misleading ones
(a step missing `id` raised a KeyError naming the *skill*). `validate_spec`
walks the spec first and reports every problem in one pass, addressed by step
position and id.
"""
from __future__ import annotations

STEP_TYPES = ("agent", "command", "retry_loop", "polling_loop", "counting_loop")


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
        prov = spec.get("provider")
        if prov is None:
            errors.append("missing top-level 'provider:' block, e.g. "
                          "provider: { type: openrouter, model: \"openai/gpt-4o-mini\" }")
        elif not isinstance(prov, dict):
            errors.append("'provider:' must be a mapping with 'type' and 'model'")
        else:
            if not prov.get("type"):
                errors.append("provider: missing 'type' (anthropic | openai | "
                              "openrouter | nvidia | local)")
            if not prov.get("model"):
                errors.append("provider: missing 'model'")
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
