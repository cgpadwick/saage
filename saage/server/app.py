"""FastAPI application for the saage server: job management, SSE streams, and flow catalog.

Usage:
    from saage.server.app import create_app, serve
"""
from __future__ import annotations

import json
import time
from pathlib import Path

try:
    from fastapi import FastAPI, Form, HTTPException, Request
    from fastapi.responses import HTMLResponse, Response, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from markupsafe import escape as _escape
except ImportError as e:      # pragma: no cover
    raise ImportError("saage.server requires: pip install saage[server]") from e

from .catalog import FlowCatalog
from .config import ServerConfig, load_server_config
from .dag import build_graph, reduce_states, render_svg
from .jobs import JobRegistry
from .parse import parse_launch

_HERE = Path(__file__).parent


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
            # Terminal drain: emit any bytes written between the last read and terminal detection.
            if path.exists():
                with open(path, "rb") as f:
                    f.seek(offset)
                    chunk = f.read()
                if chunk:
                    offset += len(chunk)
                    yield f"data: {json.dumps({'chunk': chunk.decode(errors='replace'), 'offset': offset})}\n\n"
            yield f"event: done\ndata: {json.dumps({'status': s})}\n\n"
            return
        time.sleep(0.5)


def _drain_ledger_lines(data: bytes):
    """Parse complete newline-terminated lines from *data*; return (records, consumed_bytes)."""
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return [], 0
    records = []
    for raw_line in data[:last_newline + 1].split(b"\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            records.append(json.loads(raw_line))
        except json.JSONDecodeError:
            continue
    return records, last_newline + 1


def _tail_ledger(path: Path, job_status):
    """Generator that replays a ledger file then follows it, yielding SSE events.

    Offset advances only to the end of the last successfully parsed *complete*
    line (identified by a trailing newline), so a record split across two writes
    is never consumed as a fragment.
    """
    offset = 0
    while True:
        if path.exists():
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
            if data:
                records, consumed = _drain_ledger_lines(data)
                for rec in records:
                    yield f"data: {json.dumps(rec)}\n\n"
                offset += consumed
        s = job_status()
        if s not in ("running",):
            # Terminal drain: process any complete lines written since the last poll.
            if path.exists():
                with open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read()
                if data:
                    records, _ = _drain_ledger_lines(data)
                    for rec in records:
                        yield f"data: {json.dumps(rec)}\n\n"
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

    # Static files and templates
    static_dir = _HERE / "static"
    templates_dir = _HERE / "templates"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))

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

    # ------------------------------------------------------------------
    # Page routes
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        """Home page: flow launcher and active jobs table."""
        catalog.refresh()
        flows = list(catalog.flows.values())
        return templates.TemplateResponse(request, "home.html", {"flows": flows})

    @app.get("/flow-knobs", response_class=HTMLResponse)
    def flow_knobs(request: Request, flow: str = ""):
        """Return knob form fragment for the selected flow (htmx swap target)."""
        fi = catalog.get(flow)
        if fi is None or not fi.knobs:
            return HTMLResponse("")
        lines = []
        for name, default in fi.knobs.items():
            lines.append(
                f'<label for="knob-{name}">{name}</label>'
                f'<input type="text" id="knob-{name}" name="overrides.{name}" value="{default}">'
            )
        return HTMLResponse("".join(lines))

    @app.get("/jobs-table", response_class=HTMLResponse)
    def jobs_table(request: Request):
        """Active/recent jobs table fragment (htmx poll target)."""
        jobs = registry.list()
        if not jobs:
            return HTMLResponse('<p style="color:var(--muted)">No jobs yet.</p>')
        rows = []
        for j in jobs:
            jid = j["job_id"]
            status = j.get("status", "unknown")
            rows.append(
                f'<tr>'
                f'<td><a href="/jobs/{jid}" style="color:var(--accent)">{jid[:12]}</a></td>'
                f'<td>{j.get("flow_name", "")}</td>'
                f'<td><span class="badge badge-{status}">{status}</span></td>'
                f'<td>{j.get("created_at", "")}</td>'
                f'<td>'
                + (
                    f'<button class="danger" style="padding:.2rem .6rem; font-size:.75rem;"'
                    f' hx-post="/api/jobs/{jid}/cancel"'
                    f' hx-confirm="Cancel?"'
                    f' hx-on::after-request="htmx.trigger(\'#active-jobs-table\', \'refresh\')">'
                    f'Cancel</button>'
                    if status == "running" else ""
                )
                + f'</td>'
                f'</tr>'
            )
        html = (
            '<div class="card" style="padding:0;overflow:hidden;">'
            "<table><thead><tr>"
            "<th>Job ID</th><th>Flow</th><th>Status</th><th>Started</th><th></th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
        return HTMLResponse(html)

    @app.post("/parse-form", response_class=HTMLResponse)
    def parse_form(request: Request, text: str = Form("")):
        """Parse NL text and return an HTML fragment with Confirm / Edit / Cancel."""
        if not text.strip():
            return HTMLResponse('<p class="parse-error">Please enter some text.</p>')
        try:
            prov = _get_provider()
        except HTTPException as exc:
            return HTMLResponse(f'<p class="parse-error">{exc.detail}</p>')
        result = parse_launch(text, catalog, prov)
        if not result.get("ok"):
            return HTMLResponse(
                f'<p class="parse-error">Could not parse: {_escape(result.get("error", "unknown error"))}</p>'
            )
        flow = result.get("flow", "")
        overrides = result.get("overrides", {})
        explanation = result.get("explanation", "")
        safe_flow = _escape(flow)
        safe_explanation = _escape(explanation)
        rows = "".join(
            f"<tr><td>{_escape(k)}</td><td>{_escape(v)}</td></tr>"
            for k, v in overrides.items()
        )
        hidden_inputs = f'<input type="hidden" name="flow" value="{safe_flow}">'
        hidden_inputs += "".join(
            f'<input type="hidden" name="overrides.{_escape(k)}" value="{_escape(v)}">'
            for k, v in overrides.items()
        )
        return HTMLResponse(
            f'<div class="parse-result">'
            f'<strong>{safe_flow}</strong>'
            + (f'<p>{safe_explanation}</p>' if explanation else "")
            + (f"<table><thead><tr><th>Knob</th><th>Value</th></tr></thead><tbody>{rows}</tbody></table>"
               if rows else "")
            + f'<div style="margin-top:.75rem;display:flex;gap:.5rem;">'
            f'<form hx-post="/launch-form" style="display:inline">'
            f'{hidden_inputs}'
            f'<button type="submit">Confirm</button>'
            f'</form>'
            f'<button class="secondary" onclick="document.getElementById(\'parse-preview\').innerHTML=\'\'">Cancel</button>'
            f'</div>'
            f'</div>'
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str):
        """Job detail page with live DAG and log stream."""
        entry = registry.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        flow_name = entry.get("flow_name", "")
        fi = catalog.get(flow_name)
        spec = fi.spec if fi else {}
        graph = build_graph(spec)
        graph_json = json.dumps({
            "nodes": [{"id": n.id, "type": n.type, "params": n.params} for n in graph.nodes],
        })
        return templates.TemplateResponse(request, "job.html", {
            "job_id": job_id,
            "job": entry,
            "graph_json": graph_json,
        })

    @app.get("/jobs/{job_id}/dag.svg")
    def job_dag_svg(job_id: str):
        """Return the current DAG SVG for a job."""
        entry = registry.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        flow_name = entry.get("flow_name", "")
        fi = catalog.get(flow_name)
        spec = fi.spec if fi else {}
        graph = build_graph(spec)

        run_dir = registry._home / "runs" / job_id
        ledger_path = run_dir / "ledger.jsonl"
        events: list[dict] = []
        if ledger_path.is_file():
            for raw in ledger_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass

        states = reduce_states(events)
        svg = render_svg(graph, states)
        return Response(content=svg, media_type="image/svg+xml")

    @app.post("/launch-form")
    async def launch_form(request: Request):
        """Accept form-encoded launch data and redirect to the new job page.

        Bridges the htmx knob form (which posts flat ``overrides.name=value`` fields)
        to the JSON job API.  On success returns an ``HX-Redirect`` header so htmx
        navigates the whole page; plain browsers receive a 303 Location redirect.
        """
        form = await request.form()
        flow_name = str(form.get("flow", ""))
        flow_info = catalog.get(flow_name)
        if flow_info is None:
            raise HTTPException(status_code=404, detail=f"flow {flow_name!r} not found")
        overrides: dict[str, str] = {}
        for key, value in form.items():
            if key.startswith("overrides."):
                overrides[key[len("overrides."):]] = str(value)
        try:
            job = registry.launch(flow_info, overrides)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        job_url = f"/jobs/{job.job_id}"
        # htmx full-page navigation via HX-Redirect; plain browsers via 303.
        if request.headers.get("HX-Request"):
            return Response(content="", status_code=200, headers={"HX-Redirect": job_url})
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=job_url, status_code=303)

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        """History page: all runs newest-first."""
        jobs = registry.list()
        return templates.TemplateResponse(request, "history.html", {"jobs": jobs})

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
