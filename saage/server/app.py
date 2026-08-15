"""FastAPI application for the saage server: job management, SSE streams, and flow catalog.

Usage:
    from saage.server.app import create_app, serve
"""
from __future__ import annotations

import json
import time
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse
except ImportError as e:      # pragma: no cover
    raise ImportError("saage.server requires: pip install saage[server]") from e

from .catalog import FlowCatalog
from .config import ServerConfig, load_server_config
from .jobs import JobRegistry
from .parse import parse_launch


def _tail(path: Path, job_status, since: int = 0):
    """Generator that tails a file and yields SSE events until job reaches terminal state."""
    offset = since
    while True:
        if path.exists():
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read()
            if chunk:
                offset += len(chunk)
                yield f"data: {json.dumps({'chunk': chunk.decode(errors='replace'), 'offset': offset})}\n\n"
        s = job_status()
        if s not in ("running",):
            yield f"event: done\ndata: {json.dumps({'status': s})}\n\n"
            return
        time.sleep(0.5)


def _tail_ledger(path: Path, job_status):
    """Generator that replays a ledger file then follows it, yielding SSE events."""
    offset = 0
    while True:
        if path.exists():
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
            if data:
                for raw_line in data.split(b"\n"):
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        rec = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    offset += len(raw_line) + 1
                    yield f"data: {json.dumps(rec)}\n\n"
                offset = path.stat().st_size
        s = job_status()
        if s not in ("running",):
            yield f"event: done\ndata: {json.dumps({'status': s})}\n\n"
            return
        time.sleep(0.5)


def create_app(config: ServerConfig, provider=None) -> FastAPI:
    """Create and return the FastAPI application.

    Args:
        config: Server configuration with flow paths, parser provider, host/port.
        provider: Optional LLM provider for /api/parse. If None, built lazily from
                  config.parser_provider on first request (503 if unconfigured).
    """
    app = FastAPI(title="saage server")

    catalog = FlowCatalog(config)
    catalog.refresh()
    registry = JobRegistry()

    # Lazy provider state
    _provider_box = {"provider": provider}

    def _get_provider():
        if _provider_box["provider"] is not None:
            return _provider_box["provider"]
        if config.parser_provider is None:
            raise HTTPException(
                status_code=503,
                detail="parser_provider not configured in server.yaml")
        from saage.hydrate import make_provider
        _provider_box["provider"] = make_provider(config.parser_provider)
        return _provider_box["provider"]

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/api/flows")
    def list_flows():
        """List all flows in the catalog."""
        return [
            {
                "name": fi.name,
                "description": fi.description,
                "knobs": fi.knobs,
                "error": fi.error,
            }
            for fi in catalog.flows.values()
        ]

    @app.post("/api/flows/refresh")
    def refresh_flows():
        """Refresh the flow catalog from disk."""
        catalog.refresh()
        return {"count": len(catalog.flows)}

    @app.post("/api/parse")
    def parse_text(body: dict):
        """Parse natural language text into a flow launch spec."""
        text = body.get("text", "")
        prov = _get_provider()
        return parse_launch(text, catalog, prov)

    @app.post("/api/jobs", status_code=201)
    def launch_job(body: dict):
        """Launch a flow job."""
        flow_name = body.get("flow", "")
        flow_info = catalog.get(flow_name)
        if flow_info is None:
            raise HTTPException(status_code=404, detail=f"flow {flow_name!r} not found")
        overrides = body.get("overrides") or {}
        workspace = body.get("workspace")
        try:
            job = registry.launch(flow_info, overrides, workspace=workspace)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"job_id": job.job_id, "flow": flow_name, "overrides": overrides}

    @app.get("/api/jobs")
    def list_jobs():
        """List all jobs."""
        return registry.list()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        """Get job details including shared snapshot from checkpoint.json."""
        entry = registry.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        # Attach shared snapshot from checkpoint.json if present
        run_dir = registry._home / "runs" / job_id
        cp_file = run_dir / "checkpoint.json"
        shared = None
        if cp_file.is_file():
            try:
                cp = json.loads(cp_file.read_text(encoding="utf-8"))
                shared = cp.get("shared")
            except (json.JSONDecodeError, OSError):
                pass
        return {**entry, "shared": shared}

    @app.get("/api/jobs/{job_id}/logs")
    def stream_logs(job_id: str, since: int = 0):
        """Stream job logs via SSE."""
        entry = registry.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        run_dir = registry._home / "runs" / job_id
        log_path = run_dir / "run.log"
        return StreamingResponse(
            _tail(log_path, lambda: registry.status(job_id), since=since),
            media_type="text/event-stream",
        )

    @app.get("/api/jobs/{job_id}/ledger")
    def stream_ledger(job_id: str):
        """Stream job ledger events via SSE."""
        entry = registry.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        run_dir = registry._home / "runs" / job_id
        ledger_path = run_dir / "ledger.jsonl"
        return StreamingResponse(
            _tail_ledger(ledger_path, lambda: registry.status(job_id)),
            media_type="text/event-stream",
        )

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        """Cancel a running job."""
        entry = registry.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        registry.cancel(job_id)
        return {"job_id": job_id, "status": registry.status(job_id)}

    return app


def serve(config_path=None, host=None, port=None) -> int:
    """Load config, create app, and run uvicorn. Called by `saage serve`."""
    import uvicorn

    cfg = load_server_config(config_path)
    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = port
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port)
    return 0
