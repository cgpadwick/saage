# Composable Workflow Engine (`cwe`)

A **deterministic** composable agentic workflow engine. Control flow (loops, retries,
polling, exit conditions) is owned by code — not by an LLM's judgment — while individual
steps still use LLMs to do the work.

Built on [PocketFlow](https://github.com/The-Pocket/PocketFlow) (graph + shared-store),
plus a lightweight first-class harness (file CRUD + exec + git tools) that LLM steps drive
through a native, provider-agnostic agent loop. Workflows are authored in YAML and hydrated
into runnable flows. Skills are Claude-style markdown directories, imported as-is.

## Why

Composing skills into agent files inside existing harnesses (Claude Code, Gemini, Codex,
Copilot, Windsurf) works but is non-deterministic: the harness, not your spec, decides
control flow — and it decides badly (e.g. a poll step launched in the background that never
returns). The same workflow run twice gives different results; swapping models changes
behavior entirely. This engine makes the LLM choose only *content*, never *control flow*.

## Install

Requires Python ≥ 3.10. Recommended with [uv](https://docs.astral.sh/uv/):

```bash
git clone <this-repo> && cd composable-workflow-engine
uv venv                          # create .venv/
source .venv/bin/activate
uv pip install -e ".[dev]"       # editable install + pytest
```

This installs the `cwe` CLI and the `cwe` import. `-e` (editable) makes source edits take
effect immediately; drop it for a normal install. `[dev]` adds `pytest`.

Alternatives:

```bash
pip install -e ".[dev]"          # plain pip
uv run pytest -q                 # uv as runner — no manual activate
uv run cwe run flows/story_writer/flow.yaml
```

## Quickstart

```bash
pytest -q                         # 22 tests, fully offline, no API key needed
cwe run flows/story_writer/flow.yaml          # a live run (needs a provider key, below)
cwe run flows/optimize_until_threshold/flow.yaml --set target_accuracy=0.8
```

While a flow runs, the engine logs each step to stderr as it happens — flow
loading, skills loaded, every node entering/finishing, model calls, tool calls,
and loop iterations — so you see progress instead of a silent wait. At the end it
prints a **run summary** (steps run, loop outcomes, and which files were written).
Use `-v` for tool-output detail and the full per-node results, `-q` to quiet it:

```
12:00:01  loading flow: flows/story_writer/flow.yaml
12:00:01  provider: openrouter / anthropic/claude-3.5-sonnet
12:00:01  loaded 3 skill(s): add_twist, review, write_scene
12:00:01  workflow ready: 2 top-level step(s)
12:00:01  ▶ scene  [agent: write_scene]
              ⠹ cogitating…            ← braille spinner animates during each
12:00:03      ⚙ write_file story.md       model call, then clears itself
12:00:03    ✓ scene → default
...
12:00:09  ↻ draft: iteration 1/3 done — continuing
...
12:00:30  ✓ draft: reached max_iterations (3) — exiting loop
12:00:31  run complete

── run summary ─────────────────────────────────
  steps:  scene ×3, twist ×3, critique
  loop:   draft → 3 iteration(s) (max_iterations)
  files:  review.md, story.md
────────────────────────────────────────────────
```

(Logging is configured by the CLI. As a library, `cwe` never installs log
handlers — your app controls logging via the standard `logging` module.)

## Providers

The native agent loop is provider-agnostic. Set the YAML `provider.type` and the matching
env var:

| `provider.type` | backend | env var |
|---|---|---|
| `anthropic`  | Anthropic Messages          | `ANTHROPIC_API_KEY` |
| `openai`     | api.openai.com              | `OPENAI_API_KEY` |
| `openrouter` | openrouter.ai/api/v1        | `OPENROUTER_API_KEY` |
| `local`      | any OpenAI-compatible server (Ollama/vLLM/LM Studio/llama.cpp) | none |

```yaml
provider: { type: anthropic,  model: claude-opus-4-8 }
provider: { type: openrouter, model: "anthropic/claude-3.5-sonnet" }
provider: { type: local, model: "llama3.1:8b", base_url: "http://localhost:11434/v1" }
```

### Transient-failure retries

Every real provider call is wrapped in bounded **exponential backoff with jitter**, so a
transient API failure (network blip, `429` rate limit, `5xx`) is retried instead of
aborting the whole run. Permanent errors (`400` bad request, `401` auth) are *not* retried —
they propagate immediately. Defaults: 5 attempts, 0.5s base delay doubling up to 30s. Tune
per flow with an optional `retry:` sub-block:

```yaml
provider: { type: anthropic, model: claude-opus-4-8, retry: { max_attempts: 8, base_delay: 1.0 } }
```

### Selecting provider/model from the CLI

You can override the flow's `provider` block without editing the YAML using
`--provider`, `--model`, and `--base-url`. For OpenRouter:

```bash
export OPENROUTER_API_KEY=sk-or-...
cwe run flows/story_writer/flow.yaml \
    --provider openrouter \
    --model "anthropic/claude-3.5-sonnet"      # any model id from openrouter.ai/models
```

Same idea for a local model (no key needed):

```bash
cwe run flows/story_writer/flow.yaml \
    --provider local --model "llama3.1:8b" --base-url http://localhost:11434/v1
```

The model id is whatever the backend expects — e.g. `gpt-4o` for `openai`,
`openai/gpt-4o-mini` or `meta-llama/llama-3.1-70b-instruct` for `openrouter`,
`claude-opus-4-8` for `anthropic`.

## How a workflow is built

A **flow** is a directory containing `flow.yaml` plus one sub-directory per **skill**
(`skill.md` = Claude-style frontmatter + instructions, with optional `.py` files the agent
runs via `run_command`). The YAML composes steps with three loop **primitives**:

- **`retry_loop`** — `action → check`; on `fail` loop back (with the checker's feedback fed
  in) until `pass` or `max_iterations`. *(e.g. implement → run tests)*
- **`polling_loop`** — `poll → classify`; on `running` wait and poll again until
  `complete`/`failed`, with a hard `max_wait_seconds` cap so it can never hang. *(e.g. submit
  to Slurm, poll `squeue`)*
- **`counting_loop`** — run a body of steps, looping until `max_iterations` or an `exit_when`
  predicate over the shared store. *(e.g. optimize until `accuracy >= target_accuracy`)*

Plain steps are `agent` (an LLM skill with the harness tools) and `command` (a deterministic
shell step). `set: { key: regex }` captures values from a step's output into the shared store
so `exit_when` and `{{ templates }}` can use them.

### Harness tools available to every agent

`read_file`, `write_file`, `edit_file`, `delete_file`, `run_command`, and git: `git_status`,
`git_diff`, `git_add`, `git_commit`, `git_branch`, `git_checkout`, `git_log`.

> **Security note.** The *file* tools are path-confined to the flow/workspace directory
> (`..` and absolute escapes are rejected). `run_command` and the git tools, however, run
> arbitrary shell with the engine's own privileges and `cwd` set to the workspace — they are
> **not** sandboxed and can read or modify anything the process can (e.g. `run_command` can
> `cat ../../etc/passwd`). Run untrusted flows inside a container or VM.

### `run_command` policy (denied commands)

As a first line of defense, `run_command` refuses an obviously destructive command
*before* running it — recursive force deletes (`rm -rf`), privilege escalation (`sudo`),
raw-device writes (`dd of=/dev/…`, `mkfs`), fork bombs, pipe-to-shell installs
(`curl … | sh`), reads of credential files (`/etc/shadow`, `~/.ssh/…`), and more. A
refused command is returned to the agent as an `ERROR:` (non-fatal — it just can't do
that). The full built-in denylist is `DEFAULT_DENY` in [`cwe/config.py`](cwe/config.py).

The rules are configurable via an engine config YAML (`--config engine.yaml`):

```yaml
command_policy:
  use_defaults: true            # keep the built-in denylist (default); false = start empty
  deny:                         # extra regex patterns to refuse
    - '\bkubectl\s+delete\b'
  allow:                        # regex carve-outs that override a deny match
    - '^rm -rf \./build\b'
```

```bash
cwe run flows/story_writer/flow.yaml --config engine.yaml
```

See [`engine.example.yaml`](engine.example.yaml). This is **defense in depth, not a
sandbox**: a denylist over `shell=True` can always be evaded — the real isolation
boundary is still a container/VM (above).

## Example flows (`flows/`)

Each is a runnable demo and a deterministic integration test:

| flow | demonstrates |
|---|---|
| `story_writer` | `counting_loop` with a multi-step body, then a terminal review |
| `fix_failing_test` | `retry_loop` driving real `pytest`, with feedback re-injection |
| `poll_job` | command capture + `polling_loop` + wall-clock timeout cap |
| `guessing_game` | multi-agent feedback loop: guesser + judge (higher/lower) homing in on a hidden target via `counting_loop` + `exit_when` |

## Testing

```bash
pytest -q                              # unit + integration, offline & reproducible
```

Integration tests run the real engine + real local tools/commands/files; only the LLM turns
are scripted, so the suite is free, offline, and bit-reproducible. For a real end-to-end
smoke test, run a flow live against a provider:

```bash
ANTHROPIC_API_KEY=... cwe run flows/story_writer/flow.yaml
```

(A `live` pytest marker is reserved in `pyproject.toml` for future provider-hitting tests.)

## Status

Working. ~800 lines across 9 modules. See [`docs/plan.md`](docs/plan.md) for the full design.
