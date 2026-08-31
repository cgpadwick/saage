# saage MCP client — design

Date: 2026-08-30. Status: approved in discussion (stdio-only v1).

## Goal

Let flows use external MCP servers (Reddit, web search, GitHub, …) as agent
tools, so saage can automate things that live outside the workspace. saage
already *serves* MCP (`saage mcp`); this adds the *client* direction.

## Scope (v1)

- **stdio transport only.** A server is a local subprocess (`uvx`/`npx`/
  `python -m …`) taking secrets via env vars. No HTTP, no OAuth — hosted
  connectors (claude.ai Gmail etc.) are account-bound to their host app and
  unreachable from saage by design; documented workaround is a `command` step
  invoking `claude -p`.
- Tools only. No MCP resources/prompts.
- No remote-handoff support (servers would have to exist on the remote box).

## Flow schema

```yaml
mcp:
  reddit:                        # server name: [A-Za-z0-9_-]+
    command: uvx                 # required
    args: [reddit-mcp]           # optional, list of strings
    env: [REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET]   # required env var NAMES
```

`env` lists names only — never values. At build time each name resolves
env var → credentials.toml `[mcp.<server>]` → hard error naming the fix
(`saage mcp add <server> <VAR>…`). The flow stays shareable; the machine
holds the secrets. Same resolution order as API keys.

## Engine

New module `saage/mcp_client.py`:

- `McpServerSpec` — parsed `mcp:` entry.
- `McpClient(spec, env)` — spawns the server in a dedicated daemon thread
  running its own asyncio loop (the mcp SDK is async; the engine is sync).
  One long-lived task opens `stdio_client` + `ClientSession`, initializes,
  lists tools, then parks on an event until `close()` — all context managers
  enter/exit in the same task (anyio cancel-scope rule).
- `.tools` — the server's tools wrapped as saage `Tool`s, named
  `<server>__<tool>` (e.g. `reddit__search_posts`). `run()` posts
  `call_tool` into the loop, blocks with a timeout, returns concatenated
  text content (`ERROR: …` on is_error, like every harness tool).
- Bounds: connect timeout 30s, per-call timeout 120s. `max_steps` still
  bounds the agent loop overall.

Wiring:

- `hydrate.build_flow` parses `mcp:`, resolves env, connects each server,
  passes wrapped tools via `Context.mcp_tools`. Servers are closed by
  `run_flow` in a `finally:` (clients also ride on the returned flow object
  for callers that build without running).
- **Opt-in only** (safety): MCP tools are never in the default tool set. A
  skill gets one only by naming it in its `tools:` allow-list — the same
  mechanism as `ask_user`. `AgentNode` grows an `extra_tools` parameter;
  its unknown-tool warning includes MCP tool names.
- `saage validate` schema-checks the `mcp:` block without spawning anything.

## CLI + secrets

- credentials.toml grows `[mcp.<server>]` tables mapping env-var names to
  values (chmod 600, text-splice writes like `[keys]`).
- `saage mcp` unchanged (serves; `.mcp.json` entries in the wild depend on
  it). New sibling subcommands:
  - `saage mcp add <server> <ENV_VAR>…` — prompts for each value, writes
    credentials.toml
  - `saage mcp list` — configured servers + var names (values masked)
  - `saage mcp rm <server>`
- Onboarding is error-message-driven: a flow whose secrets are missing
  fails at build time with the exact `saage mcp add` line to run.

## Demo flows

- `flows/reddit_digest/` — GridfireAI reddit-mcp (`uvx reddit-mcp`,
  script-app creds). Gather posts per subreddit → digest ranked against
  the user's interests → retry_loop checker verifies the digest cites real
  posts.
- `flows/research_report/` — tavily-mcp (`npx -y tavily-mcp@latest`,
  `TAVILY_API_KEY`). Plan queries → search/extract with notes+URLs →
  retry_loop: report with citations, checker verifies every claim has a
  source in the notes.

## Testing

- `tests/fake_mcp_server.py` — deterministic stdio MCP server (mcp SDK,
  already a dev dep) with profiles mimicking the real servers' tool names
  (`--profile echo|reddit|search`). No network.
- Unit: client connect/list/call/error/close, env resolution precedence,
  missing-secret error, validate schema errors, AgentNode opt-in gating,
  CLI add/list/rm under a temp SAAGE_HOME.
- Integration: both flows run end-to-end with the fake servers (flow_copy
  rewrites the `mcp:` command) and scripted LLM turns. Offline, free,
  bit-reproducible like the rest of the suite.

## Invariants preserved

Control flow stays YAML; MCP tools are content-level actions. All loop
bounds keep. Shared store stays JSON-serializable (clients never enter
`shared`). The denylist still governs `run_command`; MCP tools are governed
by the per-skill allow-list instead — spelled out in AGENTS.md.
