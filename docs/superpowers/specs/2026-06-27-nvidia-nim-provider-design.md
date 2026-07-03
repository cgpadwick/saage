# NVIDIA NIM provider (basic) — design

**Date:** 2026-06-27
**Status:** approved, ready for implementation

## Goal

Let a flow target NVIDIA's hosted model endpoints (NIM — `https://integrate.api.nvidia.com/v1`)
via `provider.type: nvidia`. NIM exposes an OpenAI-compatible Chat Completions API, so this
reuses the existing `OpenAIProvider` tool-use loop unchanged.

## Scope

**In scope (basic):**
- New provider type `nvidia` wired in `make_provider`.
- Points `OpenAIProvider` at the NVIDIA base URL, reads the key from `NVIDIA_API_KEY`.
- Works for any NIM model, including those that emit OpenAI-format tool calls.
- Docs + a unit test.

**Out of scope (deferred — add only if needed):**
- Capturing `reasoning_content` (chain-of-thought from Nemotron / reasoning models).
- `extra_body` knobs: `chat_template_kwargs.enable_thinking`, `reasoning_budget`.
- Passing `temperature` / `top_p` / `max_tokens`.
- Streaming consumption.
- `saage/pricing.py` entries for NIM models (cost will report 0/None until added).

Consequence of deferral: reasoning models still run and saage uses their final `content`;
the separate `reasoning_content` thinking channel is dropped, and thinking budget stays at
the NIM default.

## Changes

1. **`saage/hydrate.py` — `make_provider`**: add branch
   ```python
   if t == "nvidia":
       return OpenAIProvider(model, base_url="https://integrate.api.nvidia.com/v1",
                             api_key_env="NVIDIA_API_KEY", retry_policy=rp)
   ```
   Update the docstring enum to `anthropic | openai | openrouter | nvidia | local`.

2. **`saage/cli.py`**: add a usage example line, e.g.
   `NVIDIA_API_KEY=... saage run f.yaml --provider nvidia --model "nvidia/nemotron-3-ultra-550b-a55b"`.

3. **`AGENTS.md`**: add `nvidia` to the `provider.type` enum line.

4. **`tests/test_providers.py` (new)**: offline unit test (TDD first) asserting
   `make_provider({"type": "nvidia", "model": "x"})` returns an `OpenAIProvider`
   whose client `base_url` is the NVIDIA endpoint and whose key comes from
   `NVIDIA_API_KEY`. No network. Follows the existing offline test convention.

## Architecture / data flow

No new components. `nvidia` is a thin alias of the existing OpenAI-compatible path —
identical to how `openrouter` is wired, differing only by `base_url` and `api_key_env`.
The neutral message/tool-call format and the bounded agent loop are untouched.

## Error handling

Inherits `OpenAIProvider` behavior unchanged: `call_with_retry` backoff, and the
`EmptyResponseError` guard for HTTP-200-with-no-`choices` bodies. Missing `NVIDIA_API_KEY`
falls back to the `"not-needed"` placeholder (same as other OpenAI-compatible providers);
the NIM API then returns an auth error surfaced through the normal path.

## Testing

- Unit: the new `tests/test_providers.py` (offline).
- Full suite `pytest -q` stays green (baseline: 391 passed, 7 skipped).
- Manual end-to-end smoke (user-run, costs tokens):
  `NVIDIA_API_KEY=... saage run flows/story_writer/flow.yaml --provider nvidia --model "<nim-model>"`.
