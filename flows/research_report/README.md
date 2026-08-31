# research_report

Answers a question from the live web with a report in which **every claim is
cited and machine-verified against the collected sources** — the flagship
demo of why deterministic control flow matters: the verify step is enforced
by the engine's retry_loop, not left to one agent's diligence. Uses the
[tavily-mcp](https://github.com/tavily-ai/tavily-mcp) stdio server for
search + page extraction.

## One-time setup

1. Get a Tavily API key (free tier): https://app.tavily.com
2. Store it: `saage mcp add tavily` (from the repo root it discovers
   `TAVILY_API_KEY` from this flow and prompts; an exported `TAVILY_API_KEY`
   also works and takes precedence).
3. Node.js v20+ — the flow launches the server with `npx -y tavily-mcp@latest`.

## Run

```bash
saage run flows/research_report/flow.yaml \
  --set question="Is sodium-ion displacing lithium in grid storage?"
```

Output in the flow directory: `queries.md` (the plan), `sources.md`
(numbered sources + faithful notes), `report.md` (cited report).

## How it works

`plan` → `research` (Tavily search/extract, notes with exact URLs) →
`report_loop` retry_loop: `write_report` → `verify_report`, which fails the
loop (feedback goes back to the writer) on any uncited or unsupported claim.
Max 3 attempts.
