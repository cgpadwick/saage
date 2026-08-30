# Using saage from coding agents

saage integrates with coding agents (Claude Code, Cursor, Codex, Windsurf, …)
on two surfaces, following the same pattern tools like Graft use — a skill
teaches the agent to *author* flows, an MCP server lets it *run* them:

- **Skills** (markdown instructions the agent auto-loads) for *authoring* flows
  — writing `flow.yaml` + skill directories is file editing, which agents
  already do well; the skills carry the schema knowledge and the
  validate-test-run loop.
- **An MCP server** (`saage mcp`) for *operating* flows — list, launch,
  monitor, cancel — as native tool calls, no shell access required.

One command wires both: run **`saage setup`** and answer `y` at the
"wire up coding agents?" step. It scans for installed agents — Claude Code,
Cursor, Codex, Windsurf, Gemini CLI — shows the list with detected ones
marked, and *configures* the ones you pick (Enter takes all detected):

- **Claude Code** — installs the skills into `~/.claude/skills/` (so they work
  in every project, not just the saage repo) and registers the MCP server via
  the `claude` CLI (falling back to `~/.claude.json` directly).
- **Cursor** (`~/.cursor/mcp.json`), **Windsurf**
  (`~/.codeium/windsurf/mcp_config.json`), **Gemini CLI**
  (`~/.gemini/settings.json`) — the `saage` entry is merged into the JSON,
  everything else preserved.
- **Codex** (`~/.codex/config.toml`) — a `[mcp_servers.saage]` section is
  spliced in, comments and the rest of the file untouched.

## The MCP server (`saage mcp`)

Stdio transport; clients spawn it themselves. Flows are discovered exactly as
`saage serve` finds them: `~/.saage/server.yaml` `flow_paths`, `--flow-path`
args in the client config, or `./flows` in the directory the client runs it
from. Jobs are the same detached `saage run` subprocesses the web UI manages —
the MCP server, the web UI, and the HTTP API all see the same job list. The
provider and API key come from `saage setup`, like everywhere else. Launching
jobs requires a POSIX OS (same as `saage serve`) and the `mcp` extra
(installed by `setup.sh`; else `pip install 'saage[mcp]'`).

| tool | does |
|---|---|
| `list_flows` | flows with description + knobs (re-scans, so freshly authored flows appear) |
| `launch_flow(flow, overrides)` | start a background job, returns `job_id` immediately |
| `wait_for_job(job_id, timeout_seconds)` | **block server-side until the job finishes** — one call instead of a polling loop, and an agent blocked on a tool call burns zero tokens |
| `job_status(job_id)` | one-off check: `running` / `completed` / `failed` / `cancelled` + the job record |
| `job_logs(job_id, tail)` | last N lines of the run's engine log |
| `list_jobs` | all jobs, newest first |
| `cancel_job(job_id)` | SIGTERM the job's process group |
| `validate_flow(flow_yaml)` | free hydrate-check of a flow file — no key, no tokens |

**Token etiquette is built in**: the server's instructions and the
`launch_flow` response both tell the agent *not* to poll after launching —
flows run fine unattended. The agent is steered to ask the user whether to
wait; "yes" costs one blocking `wait_for_job` call, "no" means the job_id is
reported and checked only when the user asks. `wait_for_job` returns cleanly
(status `running`) at its timeout, so a client-side tool-call cap never turns
a long flow into an error.

Manual registration (what the wizard writes for you — use the absolute path
to `saage` inside your venv, since MCP clients won't have it on PATH):

```bash
# Claude Code
claude mcp add -s user saage /path/to/.venv/bin/saage mcp
```

```json
// Cursor: ~/.cursor/mcp.json
{"mcpServers": {"saage": {"command": "/path/to/.venv/bin/saage", "args": ["mcp"]}}}
```

Any other MCP client: command `/path/to/.venv/bin/saage`, args `["mcp"]`.

## The skills

Both live canonically in `.claude/skills/` in this repo (auto-discovered by
Claude Code when working here) and ship inside the package
(`saage/agent_assets/`) so the wizard can install them user-wide:

- **`designing-saage-flows`** — the guided flow builder. Triggers on vague
  requests ("help me design a flow", "walk me through automating X"):
  interviews the user one question at a time (goal + finish line, loop shape,
  what's deterministic, knobs, bounds), plays back a compact design summary
  for confirmation, then hands off to the authoring skill.
- **`building-saage-flows`** — the authoring loop for a known design: pick the
  primitive, write `flow.yaml` + skill dirs, hydrate-check (free), offline
  integration test with scripted LLM turns, green pytest, only then a live
  run. Also documents the traps `AGENTS.md` omits.

Agents without skill support get the same content via the pointer at the top
of [`AGENTS.md`](../AGENTS.md), which remains the full schema reference.

## The intended loop

An agent with both surfaces can go end to end: interview the user
(`designing-saage-flows`) → author files (`building-saage-flows`) →
`validate_flow` (MCP, free) → `launch_flow` → ask the user, then one
`wait_for_job` (or hand back the job_id) → report results — with saage
guaranteeing the workflow's control flow stays deterministic no matter which
agent drives it.
