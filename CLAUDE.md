# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Two audiences

- **Authoring flows/skills** (writing `flow.yaml` + `skill.md` for the engine to run):
  read **[AGENTS.md](AGENTS.md)** — a self-contained reference for the schema, step
  types, the shared store, harness tools, and conventions. Don't duplicate that here.
- **Working on the engine itself** (the Python in `saage/`): this file.

## Commands

```bash
uv pip install -e ".[dev]"        # editable install + pytest + boto3 (or: pip install -e ".[dev]")
pytest -q                          # full suite — offline, no API key, bit-reproducible
pytest tests/test_primitives.py -q # one file
pytest -q -k retry                 # by name substring
pytest tests/integration/ -q       # end-to-end flow runs (real engine, scripted LLM turns)
SAAGE_SSH_TESTS=1 pytest tests/remote/   # + live ssh handoffs to localhost (needs local sshd)
SAAGE_LIVE_PROVIDER=1 pytest -m live     # provider-hitting tests (reserved; needs API key)
```

There is no linter/formatter configured. CI (`.github/workflows/ci.yml`) runs only
`pytest -q` on Python 3.10/3.11/3.12. Match the existing terse, comment-rich style.

A live end-to-end smoke test (costs API tokens):

```bash
ANTHROPIC_API_KEY=... saage run flows/story_writer/flow.yaml
```

## Architecture

The engine turns a YAML workflow into a **PocketFlow graph over a shared dict**, where
control flow is deterministic (code) and only step *content* comes from an LLM.

**The run pipeline** (`saage/`):

1. `cli.py` — argparse entry (`saage` console script), configures logging, parses
   `--set`/`--provider`/`--config`, calls `run_flow`, prints the run summary.
2. `hydrate.py` — **the schema authority**. `build_flow()` reads the YAML, `build_step()`
   recursively maps each step `type` to a node or primitive sub-flow, chains top-level
   steps with PocketFlow's `>>`, and seeds the shared store (incl. auto-seeded
   `workspace`/`venv`/`flow_dir`/`python`). Returns `(flow, shared)`.
3. `nodes.py` — `AgentNode` and `CommandNode` (PocketFlow nodes). `render()` does the
   Jinja templating from the shared store; `set:` regex captures write results back.
4. `primitives.py` — `retry_loop` / `polling_loop` / `counting_loop` built as
   PocketFlow sub-flows wired with action-string transitions; `Subflow` wrapper.
5. `agent.py` — `run_agent()`: the provider-agnostic **tool-use loop**, bounded by
   `max_steps` so it always terminates. Tool exceptions are caught and fed back to the
   model as `ERROR:` strings rather than aborting.
6. `llm.py` — `LLMProvider` interface + `AnthropicProvider` / `OpenAIProvider`
   (OpenAI class also backs `openrouter` and `local`). `LLMResponse`/`ToolCall` types.
7. `tools.py` — the harness tools (`default_tools()`): file CRUD (path-confined to the
   workspace), `run_command`, git. `shell.py` runs commands as POSIX `sh` everywhere
   (Git Bash on Windows; `find_bash()` discovery, `SAAGE_SHELL` override).
8. `config.py` — `EngineConfig` + the `run_command` denylist policy (`DEFAULT_DENY`),
   tunable via `--config engine.yaml`. `retry.py` — provider-call backoff. `skills.py`
   — parse `skill.md`. `spinner.py` — TTY progress.

**Key invariants when editing:**
- *Determinism is the product.* Control flow (looping, polling, exit conditions, ordering)
  must stay in code/YAML. The LLM chooses content only. Don't add LLM-driven branching.
- *All run state lives in the shared store.* PocketFlow shallow-copies nodes each step, so
  nothing else persists between steps. Loop counters/feedback go through `shared`.
- *Loops always terminate.* `max_steps` (agent), `max_iterations` (retry/counting),
  `max_wait_seconds` (polling). Preserve these bounds.
- *Tool safety:* file tools are sandboxed to the workspace; `run_command`/git are **not**
  sandboxed (real shell) but screened by the denylist. The denylist is defense-in-depth,
  not a sandbox — don't claim otherwise.
- *Resumability rides on the shared store.* `saage/checkpoint.py` JSON-snapshots
  `shared` after each node (via `Subflow._orch`), tagged with `resume_step` (the
  *next* node's `_step_index`, set in `hydrate.py`). `saage resume` restores
  `shared` and sets the top-level `start_node` to `steps[resume_step]`. Because
  `resume_step` is a *top-level* step index, resume re-enters at the outermost
  loop — a nested inner loop restarts (its `_iter` is not preserved). Keep
  everything written into `shared` JSON-serializable, or checkpoints degrade to
  `str()` coercion.

**Remote handoff** (`saage/remote/`, `saage remote …` subcommand): packages a flow run as
a git ref, pushes over ssh, runs the unchanged engine under tmux on a remote box, and
observes/fetches artifacts. `handoff.py` orchestrates; `sshio.py` (binary-safe ssh I/O,
tar-into-ssh transfers — never rsync on Windows), `workspace.py` (git-ref packaging),
`observe.py` (status/logs/ps), `lambda_api.py` (Lambda Cloud provisioning),
`creds.py`/`state.py`/`target.py` (credentials.toml, run ledger, SSH targets),
`r2push.py` (R2/S3 artifact mirror). Design notes: `docs/remote_handoff_plan.md`.

## Testing conventions

- **Offline by default.** The LLM is replaced by test doubles in `tests/saage_testkit.py`:
  `RoutedProvider` routes each call to a per-skill queue keyed by a `SKILL_ID: <name>`
  marker in the skill body, so it survives loops/interleaving. Use `tool_turn()`/`resp()`/
  `call()` helpers to script turns.
- **Integration tests** (`tests/integration/`) run the *real* engine + real tools/commands/
  files against the actual flows in `flows/`; only the LLM turns are scripted — so the suite
  is free, offline, and reproducible. The `flow_copy` fixture (`tests/conftest.py`) copies a
  flow into a temp dir for hermetic runs.
- **Markers** (`pyproject.toml`): `live` (real provider, needs `SAAGE_LIVE_PROVIDER`),
  `ssh` (needs local sshd, `SAAGE_SSH_TESTS`). Both skipped otherwise.
- The `flows/` directory is dual-purpose: each flow is a runnable demo **and** a fixture for
  an integration test. Changing a flow can break its test, and vice versa.
</content>
</invoke>
