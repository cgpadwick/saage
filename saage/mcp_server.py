"""`saage mcp` — an MCP (Model Context Protocol) server over the flow job
manager, so coding agents can discover, launch, and monitor saage flows as
native tools instead of shelling out.

Thin by design: flow discovery is the server's `FlowCatalog`, execution is the
server's `JobRegistry` (each job a detached `saage run` subprocess with its own
checkpoint + run.log), so the web UI, the HTTP API, and MCP all see the same
jobs. Transport is stdio — clients spawn `saage mcp` themselves; flows are
found exactly as `saage serve` finds them (server.yaml flow_paths, --flow-path,
or ./flows in the launch directory). Registered for Claude Code by the
`saage setup` wizard; other clients get a config snippet from the same wizard.

Like `saage serve`, launching jobs needs a POSIX OS (process groups + signals).
Needs the `mcp` extra: pip install 'saage[mcp]'.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_INSTRUCTIONS = """\
saage runs deterministic multi-step LLM workflows ("flows"): control flow is
fixed YAML, only step content comes from a model. Typical use: list_flows to
see what exists, launch_flow to start one (it returns immediately with a
job_id), then poll job_status / job_logs until the status is completed or
failed. Flows can take minutes — poll, don't wait. To author a NEW flow, edit
files (flow.yaml + skill dirs) per the repo's AGENTS.md and check it with
validate_flow before launching."""


def _tail(path: Path, n: int) -> str | None:
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def build_server(config_path=None, flow_paths=None):
    from mcp.server.mcpserver import MCPServer

    from .server.catalog import FlowCatalog
    from .server.config import load_server_config, resolve_flow_paths
    from .server.jobs import JobRegistry

    cfg = resolve_flow_paths(load_server_config(config_path), flow_paths)
    catalog = FlowCatalog(cfg)
    registry = JobRegistry()
    server = MCPServer(name="saage", instructions=_INSTRUCTIONS)

    @server.tool(description="List the runnable flows: name, description, and "
                             "knobs (shared-store values overridable at launch).")
    def list_flows() -> list[dict[str, Any]]:
        catalog.refresh()               # cheap, and agents edit flows mid-session
        return [{"name": f.name, "description": f.description,
                 "knobs": f.knobs, "error": f.error}
                for f in catalog.flows.values()]

    @server.tool(description="Launch a flow as a background job. Returns a "
                             "job_id immediately — poll job_status/job_logs; "
                             "flows can take minutes. `overrides` sets knob "
                             "values (see list_flows).")
    def launch_flow(flow: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        catalog.refresh()
        info = catalog.get(flow)
        if info is None:
            known = ", ".join(sorted(catalog.flows)) or "(none found)"
            return {"error": f"unknown flow {flow!r} — known flows: {known}"}
        if info.error:
            return {"error": f"flow {flow!r} is broken: {info.error}"}
        try:
            job = registry.launch(info, {k: str(v) for k, v in (overrides or {}).items()})
        except ValueError as e:         # unknown knob — agent picked a bad override
            return {"error": str(e)}
        return {"job_id": job.job_id, "flow": job.flow_name,
                "hint": "poll job_status(job_id) until completed/failed"}

    @server.tool(description="Status of one job: running | completed | failed "
                             "| cancelled, plus its record. Poll this after "
                             "launch_flow.")
    def job_status(job_id: str) -> dict[str, Any]:
        rec = registry.get(job_id)
        if rec is None:
            return {"error": f"unknown job {job_id!r}"}
        rec["status"] = registry.status(job_id)
        return rec

    @server.tool(description="Last `tail` lines of a job's engine log "
                             "(step progress, model/tool calls, errors).")
    def job_logs(job_id: str, tail: int = 60) -> str:
        if registry.get(job_id) is None:
            return f"ERROR: unknown job {job_id!r}"
        run_dir = registry.home / "runs" / job_id
        out = _tail(run_dir / "run.log", tail) \
            or _tail(run_dir / "server_launch.log", tail)
        return out if out else "(no log output yet)"

    @server.tool(description="All jobs, newest first, with statuses.")
    def list_jobs() -> list[dict[str, Any]]:
        return registry.list()

    @server.tool(description="Cancel a running job (SIGTERM to its process "
                             "group). Returns whether a process was stopped.")
    def cancel_job(job_id: str) -> dict[str, Any]:
        if registry.get(job_id) is None:
            return {"error": f"unknown job {job_id!r}"}
        return {"cancelled": registry.cancel(job_id)}

    @server.tool(description="Validate a flow.yaml WITHOUT running it: parses "
                             "the spec, loads every skill, wires the graph. "
                             "Free — no API key, no tokens. Use after "
                             "authoring or editing a flow.")
    def validate_flow(flow_yaml: str) -> dict[str, Any]:
        import tempfile

        from .hydrate import build_flow
        path = Path(flow_yaml).expanduser()
        if not path.is_file():
            return {"ok": False, "error": f"flow file not found: {path}"}
        try:
            build_flow(path, provider=object(),
                       workspace=tempfile.mkdtemp(prefix="saage-validate-"))
        except Exception as e:  # noqa: BLE001 — the message IS the result
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    return server


def serve_mcp(config_path=None, flow_paths=None) -> int:
    """Run the stdio MCP server. Called by `saage mcp`; blocks until the
    client disconnects. All logging goes to stderr (stdout is the protocol)."""
    server = build_server(config_path, flow_paths)
    server.run("stdio")
    return 0
