# Token Usage + Cost Tracking — Design

**Status:** implemented autonomously (2026-06-24, user stepped out). Closes #30.

## Goal

Track detailed token usage of a run — counts AND estimated cost when available.
Builds on the existing process-wide `TokenUsage` (which already summed
provider-reported in/out tokens and printed a one-line summary).

## Decisions

- **Per-model breakdown.** `TokenUsage.add(usage, model)` now records usage both
  in aggregate and per model id (`by_model: {model -> _ModelUsage}`). The two
  providers pass `self.model`. A flow that uses one model (the common case) gets a
  single entry; multi-model runs are itemized.
- **Cost = grounded, never guessed.** New `saage/pricing.py` holds a built-in
  table of rough public list prices (USD per 1M input/output tokens), matched by
  **substring** against the model id (longest key wins, so `gpt-4o-mini` beats
  `gpt-4o`). `cost(model, in, out)` returns **None** for an unknown model, so a
  cost is shown only when it's grounded in a known rate. Prices are explicitly
  best-effort and change often; **overridable** via `SAAGE_PRICES` (path to a JSON
  `{"<substring>": [in_per_1M, out_per_1M]}`) that merges over the built-ins.
- **Surfaced two ways:**
  - the run summary gains a `cost: ~$X (estimated)` line (when grounded) and a
    per-model breakdown when >1 model was used;
  - the run dir (`~/.saage/runs/<id>/`, from #21) gets a **`usage.json`**:
    `{calls, prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd,
    by_model{...}}` — an inspectable artifact (write is best-effort / non-fatal).

## Out of scope

- Per-step / per-skill attribution (the ledger already records which node ran;
  usage is provider-reported per call, not per node — a future enhancement could
  tag each ledger row with its call's tokens).
- A live, always-current price feed — `SAAGE_PRICES` is the override hook.

## Tests

`tests/test_pricing.py` — substring match (+ longest-wins), cost math, unknown →
None, env override, malformed-override ignored. `tests/test_agent.py` —
per-model accumulation + grounded cost + `as_dict`, and cost None for an unpriced
model. Full suite green (375 passed, 7 skipped).
