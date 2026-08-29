# SAAGE — Super Awesome Agentic Graph Engine

[![CI](https://github.com/cgpadwick/saage/actions/workflows/ci.yml/badge.svg)](https://github.com/cgpadwick/saage/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

SAAGE is a deterministic, composable agentic workflow engine. Control flow
(loops, retries, polling, exit conditions) is owned by code, not by an LLM's judgment,
while individual steps still use LLMs to do the work. It's a graph engine: workflows are
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

Requires Python ≥ 3.10. Clone, run the setup script, done:

```bash
git clone <this-repo> && cd saage
./setup.sh                       # Linux / macOS / WSL2
source .venv/bin/activate
```

```powershell
git clone <this-repo>; cd saage
setup_windows.bat                # native Windows
.venv\Scripts\activate
```

The script creates `.venv/`, installs everything (CLI, web UI, test tools;
editable, so source edits take effect immediately), and finishes with a
`saage doctor` environment check. It's idempotent — re-run it after a `git
pull`. Prefer doing it by hand? It's just `python -m venv .venv` +
`pip install -e ".[dev,server]"` (or `uv venv` + `uv pip install`, which
`setup.sh` uses automatically when uv is installed).

**Platforms:** Linux, macOS, and Windows — both WSL2 and native. On native
Windows you also need [Git for Windows](https://git-scm.com/download/win):
flow commands are POSIX `sh` everywhere, and its bundled `bash.exe` is what
runs them (found automatically next to `git.exe`, never `System32\bash.exe`;
set `SAAGE_SHELL` to override). Don't install an rsync port — saage
deliberately never uses rsync on Windows; remote handoffs go over
tar-into-ssh and work natively.

## Quickstart

```bash
saage setup                       # one-time: pick a default provider + model, paste an API key
saage serve                       # web UI at http://127.0.0.1:8321 — browse and launch flows
```

`saage setup` is interactive (aws-configure style): it validates the key with a
cheap live call, saves the defaults to `~/.saage/config.yaml`, and the key to
`~/.saage/credentials.toml` (chmod 600). Everything — CLI runs, the web UI, its
natural-language launcher — uses those defaults from then on.

Or drive it from the command line:

```bash
saage run flows/story_writer/flow.yaml                     # a live run with your defaults
saage run flows/guessing_game/flow.yaml --set target=0.3   # override a flow knob
saage run flows/story_writer/flow.yaml --provider anthropic --model claude-opus-4-8
                                                           # different provider for one run
pytest -q                         # full test suite: offline, no API key needed
```

The example flows don't pin a provider, so they all run with whatever you chose
in setup. `export OPENROUTER_API_KEY=...` (etc.) always wins over the saved
key, and `--provider/--model` beats everything for a single run. A flow *can*
pin its own `provider: { type: ..., model: ... }` block (e.g. one that needs a
specific strong model) — a pin beats your defaults, and the model id must then
match that provider.

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
              ⠹ cogitating…               (spinner shown during each model call)
12:00:03      ⚙ write_file story.md
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

## Web UI: `saage serve`

Run the flow job manager and web UI locally, with a natural-language launcher and live job
monitoring. The server requires a POSIX OS (Linux/macOS): job control uses process groups and
POSIX signals, which are unavailable on Windows.

```bash
saage serve                          # from the repo root: ./flows is picked up automatically
# Open http://127.0.0.1:8321
```

(`setup.sh` installs the server; on a hand-rolled install without the
`[server]` extra, add it with `pip install -e ".[server]"`.)

The provider and API key come from `saage setup` — jobs and the
natural-language launcher use your saved defaults; there is nothing
LLM-related to configure on the server. With no config file, `saage serve`
auto-discovers a `flows/` directory under the current directory, and
`--flow-path DIR` (repeatable) adds any directory without a config. To pin
flow directories or the bind address persistently, write `~/.saage/server.yaml`:

```bash
cat > ~/.saage/server.yaml << 'EOF'
flow_paths:
  - ./flows                          # search these dirs for */flow.yaml
host: 127.0.0.1
port: 8321
EOF

saage serve
# Open http://127.0.0.1:8321
```

**Home page:** Lists all flows in `flow_paths` and provides two ways to launch:
1. **Knob form** — drop-down to select a flow, form fields for numeric/text parameters.
2. **Natural language** — "Run the story writer with 5 iterations" → the LLM parses it into
   flow name + knob values; you confirm before launching.

**Job detail page:** Live DAG visualization (updated as the run progresses) + streaming logs + a
cancel button.

**History page:** All past runs, newest first.

Everything the UI does is a plain JSON API — curl examples for every endpoint
in [docs/server_api.md](docs/server_api.md).

### Server config (`~/.saage/server.yaml`)

- **`flow_paths`** (list) — directories whose *immediate* subdirectories are scanned for
  `<flow_name>/flow.yaml` (one level deep, not recursive). Paths are relative to cwd or absolute.
- **`parser_provider`** (dict, optional) — advanced override: use a *different*
  LLM for natural-language parsing than your `saage setup` defaults (same shape
  as `provider:` in flow.yaml, e.g. `{ type: openrouter, model: "openai/gpt-4o-mini" }`
  for a cheaper parser model). Normally leave it unset — the parser uses the
  setup defaults; with neither, the NL launcher is disabled (503).
- **`host`** (str, default `127.0.0.1`) — bind address.
- **`port`** (int, default `8321`) — bind port.

### Internals

Jobs are run as detached subprocesses of `saage run`, so they do not block the server.
Each job has its own checkpoint and ledger at `~/.saage/runs/<job_id>/`, mirroring the
CLI's local run directories. The UI polls ledger events to render live DAG state and
streams logs via Server-Sent Events (SSE). Job cancellation sends SIGTERM to the subprocess.

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

A loop nested inside another loop isn't resumed independently: a crash redoes the
entire in-progress **outer** iteration, re-running the inner loop from scratch.
The result stays correct, but keep inner loops cheap (or prefer a single loop
level) if resumability matters.

## Providers

The native agent loop is provider-agnostic. The provider for a run is resolved as:

1. `--provider/--model/--base-url` CLI flags (single-run override), else
2. the flow's own `provider:` block (a pin — for flows that need a specific model), else
3. your `saage setup` defaults (`~/.saage/config.yaml`).

The API key comes from the provider's env var when set, else from
`~/.saage/credentials.toml` `[keys]` (written by `saage setup`, chmod 600):

| `provider.type` | backend | env var |
|---|---|---|
| `anthropic`  | Anthropic Messages          | `ANTHROPIC_API_KEY` |
| `openai`     | api.openai.com              | `OPENAI_API_KEY` |
| `openrouter` | openrouter.ai/api/v1        | `OPENROUTER_API_KEY` |
| `nvidia`     | integrate.api.nvidia.com/v1 (NIM) | `NVIDIA_API_KEY` |
| `local`      | any OpenAI-compatible server (Ollama/vLLM/LM Studio/llama.cpp) | none |

```yaml
provider: { type: anthropic,  model: claude-opus-4-8 }
provider: { type: openrouter, model: "anthropic/claude-3.5-sonnet" }
provider: { type: nvidia, model: "nvidia/nemotron-3-ultra-550b-a55b" }
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
saage run flows/story_writer/flow.yaml \
    --provider openrouter \
    --model "anthropic/claude-3.5-sonnet"      # any model id from openrouter.ai/models
```

(The key comes from `saage setup` / the env var as usual — export
`OPENROUTER_API_KEY=...` if you haven't saved one for that provider.)

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
runs via `run_command`). The YAML composes steps with three loop primitives:

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

Heavier, application-specific flows live in [`contrib/`](contrib) — currently the
le-wm world-model hill-climbs (`lewm_hillclimb`, `lewm_hillclimb_guided`).

## Remote handoff (`saage remote`)

Develop a flow locally, then hand the *entire run* off to a remote GPU box —
the node runs the unchanged engine under tmux; your machine packages, pushes,
starts, and disconnects. Any flow works remotely with zero flow edits.

```bash
saage remote init                                   # one-time: ssh key + credentials file
saage remote add-target spark --host spark.local --user saage   # any SSH-able box
saage remote handoff flows/greenfield_ml/flow.yaml --target spark \
    --set train_epochs=8                            # package, push, start, disconnect

saage remote status            # phase, heartbeat, ledger, log tail (latest run)
saage remote logs --live       # follow the engine log
saage remote ps                # every target: sessions vs local state (orphan detector)
saage remote fetch             # pull artifacts back: ./results/<run_id>/
saage remote kill <run>        # stop the run — never the box

saage remote list              # registered targets (local, no network)
saage remote cleanup           # prune stale targets: y/N prompt per target
                               #   (--check to ssh-probe first, info only;
                               #   removal only forgets the ssh entry — it
                               #   never terminates a box)
```

A killed remote run is resumable. The engine checkpoint (and any file listed in
the flow's `artifacts:`, e.g. the best model) is mirrored to R2 each sync
(changed-only — big files upload only when they change). Then:

```bash
saage remote resume <run>                 # node still up: resume in place
saage remote resume <run> --target spark  # node gone: fresh box, from the R2 checkpoint
```

Cross-box resume restores the checkpoint + mirrored artifacts from R2 and
reconstructs code from the run branch; heavy regenerable inputs (datasets) are
re-staged by the flow's `cloud_setup`, and the hill-climb continues from its
recorded `best_score`/iteration. To keep the trained best model across a box
death, list its (workspace-relative) path in the flow's `artifacts:`.

Targets are just SSH hosts (a LAN box, a hand-launched cloud instance —
`--port` and `--key` cover NAT'd ports and per-instance keys, e.g. Thunder
Compute). For Lambda Cloud there's provisioning built in:

```bash
saage remote spawn --gpu a100        # launch + register as a target (live capacity/pricing)
saage remote terminate <target>      # stops the meter (the only thing that does, on Lambda)
                                     #   and unregisters the target
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
  `contrib/lewm_hillclimb/cloud_setup.sh` — curated torch stacks via
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
saage setup                                    # once: default provider/model + key
saage run flows/story_writer/flow.yaml
```

(or point it at another provider with `--provider`/`--model`, above).

(A `live` pytest marker is reserved in `pyproject.toml` for future provider-hitting tests.)

## Status

Working and in active use. See [`docs/plan.md`](docs/plan.md) for the original design.

## License

Licensed under the [Apache License 2.0](LICENSE).
