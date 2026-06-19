# SAAGE — Super Awesome Agentic Graph Engine

[![CI](https://github.com/cgpadwick/saage/actions/workflows/ci.yml/badge.svg)](https://github.com/cgpadwick/saage/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**SAAGE** (`saage`) is a **deterministic** composable agentic workflow engine. Control flow
(loops, retries, polling, exit conditions) is owned by code — not by an LLM's judgment —
while individual steps still use LLMs to do the work. It's a *graph* engine: workflows are
hydrated into a graph of nodes over a shared store.

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
git clone <this-repo> && cd saage
uv venv                          # create .venv/
source .venv/bin/activate
uv pip install -e ".[dev]"       # editable install + pytest
```

This installs the `saage` CLI and the `saage` import. `-e` (editable) makes source edits take
effect immediately; drop it for a normal install. `[dev]` adds `pytest`.

**Platforms:** Linux, macOS, and Windows — both WSL2 and native.

### Native Windows

Flow commands are POSIX `sh` everywhere; on Windows the engine runs them
through Git Bash, so the same flow works unchanged on every OS. What you need:

- **Python ≥ 3.10** from [python.org](https://www.python.org/downloads/) or
  `winget install Python.Python.3.12` — with `python` on `PATH` (flows use
  the auto-seeded `{{ python }}` variable, which is `python` on Windows and
  `python3` elsewhere; there is no `python3.exe` on Windows).
- **[Git for Windows](https://git-scm.com/download/win)** — required anyway
  for the engine's git tools, and its bundled `bash.exe` is what runs flow
  commands. The engine finds it automatically (next to `git.exe`); it never
  uses `System32\bash.exe` (the WSL launcher). Set `SAAGE_SHELL` to a bash
  path to override discovery.

```powershell
git clone <this-repo>; cd saage
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                        # full offline suite, should be green
```

Both `saage run` and `saage remote` work natively: handoffs from Windows push
over ssh with binary-safe stdin, and transfers go over tar-into-ssh — no
extra installs beyond Git for Windows, and **don't** install an rsync port:
on Windows saage deliberately never uses rsync (cygwin/MSYS rsync mis-parses
`C:/...` paths as remote hosts). Verified live against Lambda Cloud and
Thunder Compute nodes.

Alternatives:

```bash
pip install -e ".[dev]"          # plain pip
uv run pytest -q                 # uv as runner — no manual activate
uv run saage run flows/story_writer/flow.yaml
```

## Quickstart

```bash
pytest -q                         # 22 tests, fully offline, no API key needed
saage run flows/story_writer/flow.yaml          # a live run (needs a provider key, below)
saage run flows/optimize_until_threshold/flow.yaml --set target_accuracy=0.8
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

(Logging is configured by the CLI. As a library, `saage` never installs log
handlers — your app controls logging via the standard `logging` module.)

## Resumable runs

Every `saage run` records a checkpoint under `~/.saage/runs/<run_id>/` after each
step (and each loop iteration). If the run is killed — Ctrl-C, a dead battery, an
ssh drop — pick it up where it left off:

```bash
saage runs                 # list runs: id, status, position, flow
saage resume               # resume the most recent unfinished run
saage resume <id|prefix>   # resume a specific run
saage resume --force <id>  # resume even if the flow.yaml/skills changed
```

`saage run` always starts a fresh run. Resume granularity is one iteration of the
outermost loop: a 12-iteration hill-climb killed during iteration 10 resumes at
iteration 10, keeping 1–9. The killed iteration is redone from its start, so a
flow's loop body should be safe to re-run (e.g. clean a checkpoint dir, then
train) — the example ML flows already follow this pattern.

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
saage run flows/story_writer/flow.yaml \
    --provider openrouter \
    --model "anthropic/claude-3.5-sonnet"      # any model id from openrouter.ai/models
```

Same idea for a local model (no key needed):

```bash
saage run flows/story_writer/flow.yaml \
    --provider local --model "llama3.1:8b" --base-url http://localhost:11434/v1
```

The model id is whatever the backend expects — e.g. `gpt-4o` for `openai`,
`openai/gpt-4o-mini` or `meta-llama/llama-3.1-70b-instruct` for `openrouter`,
`claude-opus-4-8` for `anthropic`.

## How a workflow is built

> Building a flow yourself (or pointing a coding agent at this repo)? See
> [`AGENTS.md`](AGENTS.md) for a complete, self-contained guide to the flow/skill
> schema, step types, the shared store, and conventions.

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

`{{ var }}` placeholders are filled from the shared store (deterministically, by the engine —
the model only ever sees finished text) in every step's text: a `command:` run string and an
agent skill's **description and body**. So a skill can say `Answer this question: {{ question }}`
in its instructions. An undefined name renders to `""` and logs a warning; wrap a literal brace
in `{% raw %}…{% endraw %}`.

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
that). The full built-in denylist is `DEFAULT_DENY` in [`saage/config.py`](saage/config.py).

The rules are configurable via an engine config YAML (`--config engine.yaml`):

```yaml
command_policy:
  use_defaults: true            # keep the built-in denylist (default); false = start empty
  deny:                         # extra regex patterns to refuse
    - '\bkubectl\s+delete\b'
  allow:                        # whole-command carve-outs (must match the FULL command)
    - 'rm -rf \./build'
```

```bash
saage run flows/story_writer/flow.yaml --config engine.yaml
```

An `allow` is a *whole-command* carve-out — it overrides a deny only when it matches the
**entire** command, so it can't wave through a chained extra (`rm -rf ./build && rm -rf /`
stays blocked). The policy guards the agent's `run_command` tool, where the LLM picks the
command; deterministic `command:` steps are author-written and run unfiltered.

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
| `greenfield_ml` | full ML auto-research: baseline classifier + hill-climb on MNIST |
| `lewm_hillclimb` | brownfield auto-research on an existing repo (le-wm), incl. `cloud_setup.sh` for remote GPU boxes |

## Remote handoff (`saage remote`)

Develop a flow locally, then hand the *entire run* off to a remote GPU box —
the node runs the unchanged engine under tmux; your machine packages, pushes,
starts, and disconnects. Any flow works remotely with zero flow edits.

```bash
saage remote init                                   # one-time: ssh key + credentials file
saage remote add-target spark --host spark.local --user saage   # any SSH-able box
saage remote handoff flows/greenfield_ml/flow.yaml --target spark \
    --set train_epochs=8                            # the button

saage remote status            # phase, heartbeat, ledger, log tail (latest run)
saage remote logs --live       # follow the engine log
saage remote ps                # every target: sessions vs local state (orphan detector)
saage remote fetch             # pull artifacts back: ./results/<run_id>/
saage remote kill <run>        # stop the run — never the box
```

Targets are just SSH hosts (a LAN box, a hand-launched cloud instance —
`--port` and `--key` cover NAT'd ports and per-instance keys, e.g. Thunder
Compute). For Lambda Cloud there's provisioning built in:

```bash
saage remote spawn --gpu a100        # launch + register as a target (live capacity/pricing)
saage remote terminate <target>      # stops the meter (the only thing that does, on Lambda)
```

How it works, briefly:

- **Workspace packaging — a git ref, not files.** Brownfield flows (whose
  `workspace:` is an existing repo) get a `saage-run-<id>` branch: pushed to
  `origin` when possible, `git bundle` fallback otherwise. Uncommitted
  changes: `--dirty abort` (default) / `commit` (snapshot them, your checkout
  untouched) / `ship-head` (package HEAD; for workspaces under active use).
- **Per-run secrets** (LLM key for the flow's provider, repo token) travel
  over ssh stdin into a 0600 `run_env` that is deleted when the run stops.
- **Artifacts**: a sidecar collects ledgers/reports into the node's run dir
  (`~/.saage_runs/<id>/artifacts/`); with a `[storage]` section in
  `~/.saage/credentials.toml` they also mirror to R2/S3, and `status`/`fetch`
  fall back to the mirror when the node is gone. A watchdog stops wedged runs.
- **Flow env setup**: `--ws-setup "bash ../flow/cloud_setup.sh"` runs a
  flow-supplied script inside the workspace at bootstrap (see
  `flows/lewm_hillclimb/cloud_setup.sh` — curated torch stacks via
  [ml-frameworks](https://github.com/cgpadwick/ml-frameworks) with
  driver-aware CUDA selection, dataset staging from HF, headless-EGL libs).

Design + field notes: [`docs/remote_handoff_plan.md`](docs/remote_handoff_plan.md).

## Testing

```bash
pytest -q                              # unit + integration, offline & reproducible
SAAGE_SSH_TESTS=1 pytest tests/remote/ # + live ssh handoffs to localhost
```

Integration tests run the real engine + real local tools/commands/files; only the LLM turns
are scripted, so the suite is free, offline, and bit-reproducible. For a real end-to-end
smoke test, run a flow live against a provider:

```bash
ANTHROPIC_API_KEY=... saage run flows/story_writer/flow.yaml
```

(A `live` pytest marker is reserved in `pyproject.toml` for future provider-hitting tests.)

## Status

Working. ~800 lines across 9 modules. See [`docs/plan.md`](docs/plan.md) for the full design.

## License

Licensed under the [Apache License 2.0](LICENSE).
