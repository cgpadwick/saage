"""Best-effort USD cost estimates for LLM token usage.

Prices change often and vary by provider/tier — these are rough public list
prices for common models, matched by substring against the model id. `cost()`
returns None for an unknown model rather than guessing, so a cost is only ever
shown when it's grounded in a known rate.

Override or extend via the `SAAGE_PRICES` env var: a path to a JSON file
`{"<model substring>": [<usd_per_1M_input>, <usd_per_1M_output>], ...}`. Overrides
merge over (and win ties against) the built-in table.
"""
from __future__ import annotations

import json
import os

# USD per 1,000,000 tokens: (input, output). Substring-matched (case-insensitive)
# against the model id; the LONGEST matching key wins (so "gpt-4o-mini" beats
# "gpt-4o"). Rough public list prices as of mid-2026 — update as they change.
_PRICES: dict[str, tuple[float, float]] = {
    "deepseek": (0.27, 1.10),
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.0, 8.0),
    "o3-mini": (1.10, 4.40),
    "o3": (2.0, 8.0),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.0),
}


def _overrides() -> dict[str, tuple[float, float]]:
    path = os.environ.get("SAAGE_PRICES")
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:                            # unreadable / non-JSON file: ignore
        return {}
    out: dict[str, tuple[float, float]] = {}
    for k, v in (raw.items() if isinstance(raw, dict) else ()):
        try:                                     # skip ONE malformed entry rather
            out[k.lower()] = (float(v[0]), float(v[1]))  # lowercase key: rates()
            #                                              matches against a lowercased
            #                                              model id, so a mixed-case
            #                                              override key would never hit
        except AttributeError:                   # non-str key
            continue
        except (TypeError, ValueError, IndexError, KeyError):  # skip ONE malformed
            continue                             # entry, don't drop ALL overrides
    return out


def rates(model: str) -> tuple[float, float] | None:
    """(usd_per_1M_input, usd_per_1M_output) for a model id, or None if unknown.
    The longest matching substring key wins; on a length tie the later key in the
    merged table wins — overrides are merged last, so a SAAGE_PRICES entry wins a
    tie against a built-in (the documented 'overrides win ties')."""
    table = {**_PRICES, **_overrides()}
    m = (model or "").lower()
    best_key = None
    for key in table:
        if key in m and (best_key is None or len(key) >= len(best_key)):
            best_key = key
    return table[best_key] if best_key is not None else None


def cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimated USD for the given usage, or None if the model isn't priced."""
    r = rates(model)
    if r is None:
        return None
    return prompt_tokens / 1e6 * r[0] + completion_tokens / 1e6 * r[1]
