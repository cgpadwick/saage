# saage server: local job manager for flows

A web server (`saage serve`) that discovers flows, launches them as jobs from a
GUI, plain HTTP, or natural language, and shows live progress — logs and an
animated DAG — for running and historical runs.

Target use case: *"run the lof_issue_resolver flow on issue #14 in repo X"* typed
into a box (later: Slack), parsed by an LLM into a concrete launch plan,
confirmed by the user, executed locally, observable live, cancellable.

## Principles

- **Thin server over what saage already persists.** Runs already checkpoint to
  `~/.saage/runs/<run_id>/` (`checkpoint.json`, `ledger.jsonl`, `run.log`).
  The server launches subprocesses and *reads* that store; it does not invent a
  parallel state system.
- **Deterministic control, LLM content** (the saage doctrine, applied to the
  server itself): the LLM proposes a launch plan; deterministic code validates
  it against the discovered flow catalog and a human confirms before launch.
- **v1 is a working prototype**, not mle-beast polish. Server-rendered HTML +
  htmx, one CSS file, no JS build step. Prettiness is a later, additive pass.

## Architecture

```
saage serve  (FastAPI + uvicorn, 127.0.0.1:8321, no auth in v1)
 ├─ discovery: scan flow dirs from ~/.saage/server.yaml → flow catalog
 ├─ launcher:  subprocess per job: `saage run <flow> --run-id <id> --set k=v ...`
 │             own process group; cancel = SIGTERM group, SIGKILL after grace
 ├─ registry:  ~/.saage/server/jobs.jsonl  (job → run_id, pid, flow, overrides)
 │             lets the server re-attach after restart + list its own launches
 └─ readers:   tail run.log and ledger.jsonl → SSE streams; checkpoint.json → status
```

- New subpackage `saage/server/`; installed via optional extra
  `pip install saage[server]` (fastapi, uvicorn, jinja2). Core engine deps
  unchanged.
- Subprocess-per-run (not threads): full isolation, killable, crash-proof
  server, and the run dir already captures all output. A cancelled run remains
  resumable via the existing `saage resume`.
- Job status is derived, never duplicated: `running` while the pid is alive,
  then whatever `checkpoint.json.status` says (`completed`/`failed`/...);
  `cancelled` if the server killed it.

### Flow discovery

`~/.saage/server.yaml`:

```yaml
flow_paths:
  - ~/code/saage/flows
  - ~/code/lof-agent-runner/flows
parser_provider:          # LLM used ONLY for natural-language launch parsing
  type: anthropic
  model: claude-sonnet-4-6
```

The server scans `<path>/*/flow.yaml`, hydrates each (free validation; broken
flows are listed as broken, not hidden), and caches per flow: name, first
comment block as description, the `shared:` defaults (= the overridable knobs),
and the step graph for DAG rendering. A refresh endpoint/button rescans.

## API

Curl-able from day one; OpenAPI docs at `/docs`.

| Route | What |
|---|---|
| `GET /api/flows` | catalog: flows + their knobs (shared defaults) |
| `POST /api/parse` | `{text}` → LLM-proposed launch plan (nothing launches) |
| `POST /api/jobs` | `{flow, overrides, workspace?}` → validate, launch, return job id |
| `GET /api/jobs` | all jobs (registry ∪ run store), newest first |
| `GET /api/jobs/{id}` | status, flow, overrides, shared-store snapshot |
| `GET /api/jobs/{id}/logs` | SSE tail of `run.log` (`?since=` offset for polling) |
| `GET /api/jobs/{id}/ledger` | SSE of ledger events (drives the DAG; replays first) |
| `POST /api/jobs/{id}/cancel` | SIGTERM process group → SIGKILL after grace |
| `POST /api/flows/refresh` | rescan flow paths |

### Natural-language launch

1. `POST /api/parse` prompts the parser LLM with the flow catalog (names,
   descriptions, knob names + defaults) and the user text; demands strict JSON
   `{flow, overrides, explanation}`.
2. Deterministic validation: unknown flow or unknown override key → parse
   rejected with the error shown verbatim; the parser maps to *existing* knobs
   only and never invents flow behavior. Sweeps ("batch 2–32, 5 experiments")
   resolve to whatever knobs the target flow actually exposes.
3. The UI renders the resolved plan — flow, every `--set`, the explanation —
   with **Confirm / Edit / Cancel**. Only Confirm posts to `/api/jobs`.

This confirm-gated parse is also the contract a future Slack/text adapter
consumes ("reply 'go' to launch"); Slack itself is out of scope for v1.

## UI

Jinja2 + htmx, three pages:

1. **Home / launch** — flow dropdown with the flow's knobs as an editable
   key/value form, plus the NL text box (parse → inline plan → confirm).
   Below: active jobs (status chip, flow, elapsed, cancel), then recent runs.
2. **Job detail** — live DAG + log tail side by side.
   - **DAG**: server walks the flow spec (recursing into loop `body`/`action`/
     `check`) into nodes + edges; loops render as clustered boxes (retry_loop
     shows action⇄check with an ×N badge). SVG generated server-side with a
     simple layered layout — saage graphs are linear chains with nested
     clusters, so no JS graph library.
   - **Liveness**: htmx SSE swaps node classes from ledger events — pending /
     running (pulse) / done / failed-retry (amber), with per-node attempt
     counts inside loops.
   - **Node click** → parameter panel: step type, skill or templated `run:`
     line, `set:` captures; once executed, the ledger adds resolved values,
     duration, and attempt history.
   - Works identically for finished runs (ledger replay) — including runs the
     server didn't launch.
3. **History** — everything in `~/.saage/runs` via the existing `list_runs()`,
   linking into the same job-detail view.

## Engine changes (kept minimal, all additive)

1. **Node-start ledger events.** Today ledger entries append on node
   completion; the DAG needs "running". Add a start-event append (same jsonl,
   `phase: start|end` field). Additive; existing consumers unaffected.
2. **`--run-id` flag on `saage run`** so the server can name the run dir
   before the process starts (needed to attach log/ledger streams
   immediately). Engine already accepts an explicit run id internally.
3. Nothing else. DAG topology comes from the YAML; params come from the spec +
   ledger. No UI hooks inside node classes in v1 — if later views need more,
   they extend the ledger event schema, not the classes.

## Error handling

- Launch failures (bad flow, dead venv) surface the subprocess's first stderr
  lines in the job row; the job records `failed`.
- Parse failures show the validator's exact complaint; the user can edit the
  plan manually in the form and launch anyway.
- Server restart: registry re-attach by pid; a pid that died while the server
  was down gets its status from `checkpoint.json` (crashed runs show as
  resumable, mirroring `saage runs`).
- SSE streams end with an explicit `done` event when the run reaches a
  terminal status.

## Testing

- **Unit**: catalog discovery (good/broken flows), plan validation (unknown
  flow/key rejection, sweep expansion), job registry round-trip, DAG builder
  (loop nesting → expected nodes/edges/clusters), ledger→DAG state reducer.
- **API**: FastAPI TestClient against a tmp `~/.saage` (env-pointed) and a
  stub flow that sleeps/echoes — launch, status transitions, cancel kills the
  process group, log/ledger SSE deliver and terminate.
- **Parse**: fake provider returning scripted JSON (valid, invalid-flow,
  invalid-key, non-JSON) — assert validation verdicts; no live LLM in tests.
- **No browser tests in v1**; the UI is thin over the tested API.

## Out of scope for v1

Slack/text adapters; auth; remote/multi-host execution (saage `remote` exists
separately); run comparison views; editing flows from the UI; mle-beast-level
visual polish; queueing/concurrency limits (v1 launches immediately;
`max_concurrent` is a natural v2 knob).
