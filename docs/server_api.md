# saage server HTTP API

The `saage serve` web UI is a thin client over this JSON API — everything the
UI does can be scripted with curl. Base URL: `http://127.0.0.1:8321` (see the
README's server config section for host/port).

List all flows:
```bash
curl http://127.0.0.1:8321/api/flows
```

Parse natural language to a launch spec:
```bash
curl -X POST http://127.0.0.1:8321/api/parse \
  -H "Content-Type: application/json" \
  -d '{"text": "Run story writer with 5 iterations"}'
```

Launch a job (with knob overrides):
```bash
curl -X POST http://127.0.0.1:8321/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"flow": "story_writer", "overrides": {"iterations": "5"}}'
```

List all jobs:
```bash
curl http://127.0.0.1:8321/api/jobs
```

Get job status and shared-store snapshot:
```bash
curl http://127.0.0.1:8321/api/jobs/<job_id>
```

Stream job logs (Server-Sent Events):
```bash
curl http://127.0.0.1:8321/api/jobs/<job_id>/logs
```

Cancel a running job:
```bash
curl -X POST http://127.0.0.1:8321/api/jobs/<job_id>/cancel
```

Stream ledger events (Server-Sent Events):
```bash
curl http://127.0.0.1:8321/api/jobs/<job_id>/ledger
```

Get live DAG visualization (SVG):
```bash
curl http://127.0.0.1:8321/jobs/<job_id>/dag.svg
```

**Note:** `POST /launch-form` is a UI-internal form-encoded endpoint used by the browser UI; no direct API call needed.

