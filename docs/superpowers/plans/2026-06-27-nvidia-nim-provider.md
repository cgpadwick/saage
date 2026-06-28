# NVIDIA NIM Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `nvidia` provider type so flows can run NVIDIA NIM models through the existing OpenAI-compatible loop.

**Architecture:** NIM (`https://integrate.api.nvidia.com/v1`) is OpenAI-compatible, so `nvidia` is a thin alias of the existing `OpenAIProvider` path — identical to how `openrouter` is wired, differing only by `base_url` and `api_key_env` (`NVIDIA_API_KEY`). No new components; the neutral message/tool-call format and bounded agent loop are untouched.

**Tech Stack:** Python 3.10+, `openai` SDK (already a dep), pytest. Offline tests.

## Global Constraints

- Tests are offline, no API key, bit-reproducible (`pytest -q` must stay green; baseline 391 passed, 7 skipped).
- No linter/formatter — match existing terse, comment-rich style.
- Reasoning capture, `extra_body`/`reasoning_budget`, temperature/top_p/max_tokens, streaming, and pricing entries are OUT of scope (deferred).

---

### Task 1: Wire `nvidia` provider type (with test + docs)

**Files:**
- Create: `tests/test_providers.py`
- Modify: `saage/hydrate.py` (`make_provider`, lines ~35-56)
- Modify: `saage/cli.py` (module docstring usage examples, lines ~1-8)
- Modify: `AGENTS.md` (provider.type enum, line ~55)

**Interfaces:**
- Consumes: `saage.hydrate.make_provider(spec: dict)` — maps `spec["type"]` + `spec["model"]` (+ optional `spec["retry"]`) to a provider object.
- Consumes: `saage.llm.OpenAIProvider(model, base_url=None, api_key_env="OPENAI_API_KEY", retry_policy=None)` — stores an `openai.OpenAI` client at `.client` exposing `.base_url` and `.api_key`.
- Produces: `make_provider({"type": "nvidia", "model": m})` returns an `OpenAIProvider` whose client `base_url` is `https://integrate.api.nvidia.com/v1` and whose `api_key` is read from env `NVIDIA_API_KEY`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_providers.py`:

```python
"""make_provider wiring for OpenAI-compatible providers (offline, no network)."""
from saage.hydrate import make_provider
from saage.llm import OpenAIProvider


def test_nvidia_provider_base_url_and_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-123")
    p = make_provider({"type": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"})
    assert isinstance(p, OpenAIProvider)
    assert p.model == "nvidia/nemotron-3-ultra-550b-a55b"
    # openai client exposes base_url (httpx URL) and api_key
    assert str(p.client.base_url).startswith("https://integrate.api.nvidia.com/v1")
    assert p.client.api_key == "test-key-123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers.py -q`
Expected: FAIL — `make_provider` raises `ValueError: unknown provider type: 'nvidia'`.

- [ ] **Step 3: Add the `nvidia` branch in `make_provider`**

In `saage/hydrate.py`, add the branch directly after the `openrouter` branch (before `local`):

```python
    if t == "nvidia":
        return OpenAIProvider(model, base_url="https://integrate.api.nvidia.com/v1",
                              api_key_env="NVIDIA_API_KEY", retry_policy=rp)
```

And update the docstring's first line from:

```python
    """Out of the box: anthropic | openai | openrouter | local.
```

to:

```python
    """Out of the box: anthropic | openai | openrouter | nvidia | local.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Update CLI usage example**

In `saage/cli.py` module docstring, after the existing `OPENROUTER_API_KEY=...` example line, add:

```python
  NVIDIA_API_KEY=... saage run f.yaml --provider nvidia --model "nvidia/nemotron-3-ultra-550b-a55b"
```

- [ ] **Step 6: Update AGENTS.md provider enum**

In `AGENTS.md`, change the provider.type enum line from:

```
- `provider.type` ∈ `anthropic | openai | openrouter | local`; optional
```

to:

```
- `provider.type` ∈ `anthropic | openai | openrouter | nvidia | local`; optional
```

- [ ] **Step 7: Run full suite to confirm no regressions**

Run: `pytest -q`
Expected: PASS — 392 passed, 7 skipped (one more than the 391 baseline).

- [ ] **Step 8: Commit**

```bash
git add tests/test_providers.py saage/hydrate.py saage/cli.py AGENTS.md
git commit -m "feat: add nvidia NIM provider type"
```

---

## Manual end-to-end verification (out of automated scope, user-run)

Costs API tokens; requires a real key. Not part of the offline suite:

```bash
NVIDIA_API_KEY=... saage run flows/story_writer/flow.yaml \
  --provider nvidia --model "nvidia/nemotron-3-ultra-550b-a55b"
```

Confirm the flow completes and uses the model's final answer. (Reasoning/thinking output is intentionally dropped in this basic version.)
