"""USD cost estimates for LLM token usage from USER-SUPPLIED rates.

There is deliberately no built-in price table: the one saage used to ship went
stale within weeks and mis-priced a run by 3x (and a list price is not what a
router bills anyway — routed provider and cache discounts change it either
way). The authoritative number is the provider-reported cost recorded in
`TokenUsage.billed` (OpenRouter returns it per call); this module only exists
for providers that report nothing.

Configure rates via the `SAAGE_PRICES` env var: a path to a JSON file
`{"<model substring>": [<usd_per_1M_input>, <usd_per_1M_output>], ...}`. The
longest matching substring wins. `cost()` returns None for any model without a
configured rate rather than guessing, so an estimate is only ever shown when
it's grounded in a rate the user set.
"""
from __future__ import annotations

import json
import os

# Built-in rates: none (see module docstring). Kept as a dict so SAAGE_PRICES
# merges over it unchanged.
_PRICES: dict[str, tuple[float, float]] = {}


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
    merged table wins."""
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
