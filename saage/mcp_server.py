"""`saage mcp` — an MCP (Model Context Protocol) server over the flow job
manager, so coding agents can discover, launch, and monitor saage flows as
native tools instead of shelling out.

Thin by design: flow discovery is the server's `FlowCatalog`, execution is the
server's `JobRegistry` (each job a detached `saage run` subprocess with its own
checkpoint + run.log), so the web UI, the HTTP API, and MCP all see the same
jobs. Transport is stdio — clients spawn `saage mcp` themselves; flows are
found exactly as `saage serve` finds them (server.yaml flow_paths, --flow-path,
or ./flows in the launch directory). The `saage setup` wizard registers this
server with whichever agents the user picks (see saage.agents).

Like `saage serve`, launching jobs needs a POSIX OS (process groups + signals).
Needs the `mcp` extra: pip install 'saage[mcp]'.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_INSTRUCTIONS = """\
saage runs deterministic multi-step LLM workflows ("flows"): control flow is
fixed YAML, only step content comes from a model. list_flows shows what
exists; launch_flow starts one in the background and returns a job_id
immediately.

After launching, DO NOT poll job_status/job_logs in a loop — each round costs
the user tokens while the flow runs fine on its own. Instead ASK THE USER
whether they want to wait for the result. If yes, make ONE wait_for_job call
(it blocks server-side until the job finishes — no tokens are spent while
blocked). If no, just report the job_id and check job_status later when the
user asks. To author a NEW flow, edit files (flow.yaml + skill dirs) per the
repo's AGENTS.md and check it with validate_flow before launching.

HOST MODE (no API key — you are the LLM): start_hosted_run starts a flow
whose agent steps are handed to YOU. Then loop: next_task → do that step
(ideally in a fresh subagent: each saage step expects a clean context) →
submit_result with ONLY the final answer the task asks for → next_task …
until it returns done. The engine runs the command steps, counts the loops
and decides when to stop — do not skip, reorder, or run them yourself."""


def _tail(path: Path, n: int) -> str | None:
    """Last n lines (clamped to [1, 1000]) streamed through a bounded deque —
    run.log grows without bound on long flows, and lines[-0:] would have
    returned the entire file for tail=0."""
    if not path.is_file():
        return None
    n = max(1, min(int(n), 1000))
    with open(path, encoding="utf-8", errors="replace") as fh:
        return "".join(deque(fh, maxlen=n)).rstrip("\n")


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

    def _launch(flow: str, overrides: dict[str, Any] | None,
                provider: str | None = None):
        """The Job, or an {error} dict for the agent."""
        catalog.refresh()
        info = catalog.get(flow)
        if info is None:
            known = ", ".join(sorted(catalog.flows)) or "(none found)"
            return {"error": f"unknown flow {flow!r} — known flows: {known}"}
        if info.error:
            return {"error": f"flow {flow!r} is broken: {info.error}"}
        try:
            # JSON-encode non-string values (True -> "true", None -> "null",
            # lists/dicts -> JSON) so the CLI's --set parser reconstructs the
            # original type; str(True) would arrive as the string "True"
            return registry.launch(info, {
                k: v if isinstance(v, str) else json.dumps(v)
                for k, v in (overrides or {}).items()}, provider=provider)
        except ValueError as e:         # unknown knob — agent picked a bad override
            return {"error": str(e)}

    @server.tool(description="Launch a flow as a background job; returns a "
                             "job_id immediately. `overrides` sets knob "
                             "values (see list_flows). Then ASK THE USER "
                             "whether to wait: one wait_for_job call if yes, "
                             "otherwise just report the job_id. Never poll "
                             "job_status in a loop — it wastes the user's "
                             "tokens while the flow runs by itself.")
    def launch_flow(flow: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        job = _launch(flow, overrides)
        if isinstance(job, dict):
            return job
        return {"job_id": job.job_id, "flow": job.flow_name,
                "hint": "running in the background — ask the user whether to "
                        "wait (one wait_for_job call) or report the job_id "
                        "and check later; don't poll in a loop"}

    @server.tool(description="Wait (blocking, server-side) until a job "
                             "finishes, then return its status + log tail. "
                             "ONE of these replaces a whole polling loop and "
                             "costs no tokens while blocked. Returns with "
                             "status 'running' after `timeout_seconds` if the "
                             "job is still going — call again to keep "
                             "waiting, or stop and check later.")
    def wait_for_job(job_id: str, timeout_seconds: int = 120) -> dict[str, Any]:
        import time
        if registry.get(job_id) is None:
            return {"error": f"unknown job {job_id!r}"}
        deadline = time.monotonic() + max(1, min(int(timeout_seconds), 3600))
        while True:
            status = registry.status(job_id)
            if status != "running" or time.monotonic() >= deadline:
                break
            time.sleep(1.0)
        run_dir = registry.home / "runs" / job_id
        tail_txt = _tail(run_dir / "run.log", 20) \
            or _tail(run_dir / "server_launch.log", 20) or ""
        out: dict[str, Any] = {"status": status, "log_tail": tail_txt}
        if status == "running":
            out["hint"] = ("still running after the timeout — call "
                           "wait_for_job again to keep waiting, or report "
                           "back to the user and check later")
        return out

    @server.tool(description="Status of one job: running | completed | failed "
                             "| cancelled, plus its record. For a one-off "
                             "check when the user asks — to wait for a "
                             "result, use wait_for_job instead of polling "
                             "this in a loop.")
    def job_status(job_id: str) -> dict[str, Any]:
        rec = registry.get(job_id)         # get() derives 'status' itself
        if rec is None:
            return {"error": f"unknown job {job_id!r}"}
        return rec

    @server.tool(description="Last `tail` lines (max 1000) of a job's engine "
                             "log (step progress, model/tool calls, errors).")
    def job_logs(job_id: str, tail: int = 60) -> dict[str, Any]:
        if registry.get(job_id) is None:
            return {"error": f"unknown job {job_id!r}"}
        run_dir = registry.home / "runs" / job_id
        out = _tail(run_dir / "run.log", tail) \
            or _tail(run_dir / "server_launch.log", tail)
        return {"log_tail": out if out else "(no log output yet)"}

    @server.tool(description="All jobs, newest first, with statuses.")
    def list_jobs() -> list[dict[str, Any]]:
        return registry.list()

    @server.tool(description="Cancel a running job (SIGTERM to its process "
                             "group). Returns whether a process was stopped.")
    def cancel_job(job_id: str) -> dict[str, Any]:
        if registry.get(job_id) is None:
            return {"error": f"unknown job {job_id!r}"}
        return {"cancelled": registry.cancel(job_id)}

    def _hosted(job_id: str) -> Path | None:
        """The run dir of a host-mode run — however it was started (this
        server, `saage host start`, `saage run --provider host`, resume)."""
        from .host import mailbox_dir
        if not job_id or Path(job_id).name != job_id or job_id in (".", ".."):
            return None                     # an id, never a path out of runs/
        run_dir = registry.home / "runs" / job_id
        known = mailbox_dir(run_dir).is_dir() or registry.get(job_id) is not None
        return run_dir if known else None

    def _cancelled(job_id: str) -> bool:
        return bool((registry.get(job_id) or {}).get("cancelled"))

    @server.tool(description="Start a flow in HOST MODE: no API key; every "
                             "agent step is handed to you via next_task. "
                             "Returns a job_id; then loop next_task → do the "
                             "step → submit_result until next_task says done.")
    def start_hosted_run(flow: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        from .host import mailbox_dir
        job = _launch(flow, overrides, provider="host")
        if isinstance(job, dict):
            return job
        # findable by `saage host` at once, before the child has hydrated
        mailbox_dir(registry.home / "runs" / job.job_id).mkdir(parents=True, exist_ok=True)
        return {"job_id": job.job_id, "flow": job.flow_name,
                "hint": "call next_task to get the first agent step"}

    @server.tool(description="Block (server-side, no tokens) until the hosted "
                             "run needs its next agent step, then return it: "
                             "seq, step_id, skill, system (the skill's "
                             "instructions), task, tools (the tools the skill "
                             "is meant to use), workspace (the dir relative "
                             "paths in the task refer to). Returns {done: true, status} "
                             "once the run finishes, or {waiting: true} if "
                             "the engine is still busy after timeout_seconds "
                             "(e.g. a long command step) — call again.")
    def next_task(job_id: str, timeout_seconds: int = 300) -> dict[str, Any]:
        from .host import wait_next
        run_dir = _hosted(job_id)
        if run_dir is None:
            return {"error": f"unknown job {job_id!r}"}
        kind, val = wait_next(run_dir, max(1, min(int(timeout_seconds), 3600)))
        if kind == "task":
            return {**val, "hint": "do this step, then submit_result "
                    "with its seq and ONLY the final answer the task asks for"}
        if kind == "done":
            if val == "crashed" and _cancelled(job_id):
                val = "cancelled"           # the registry knows why it died —
                                            # only a label: liveness is the
                                            # engine lock, so a resumed run of
                                            # a cancelled job drives normally
            return {"done": True, "status": val,
                    "log_tail": _tail(run_dir / "run.log", 20)
                    or _tail(run_dir / "server_launch.log", 20) or ""}
        return {"waiting": True, "status": val,
                "hint": "engine is busy (command step?) — call next_task again"}

    @server.tool(description="Answer the hosted run's pending agent step "
                             "`seq` with its final output text. The engine "
                             "applies the step's captures/routing and moves "
                             "on; call next_task for the next step.")
    def submit_result(job_id: str, seq: int, output: str) -> dict[str, Any]:
        from .host import submit
        run_dir = _hosted(job_id)
        if run_dir is None:
            return {"error": f"unknown job {job_id!r}"}
        try:
            submit(run_dir, int(seq), output)
        except ValueError as e:     # stale/duplicate/dead-run — never misapplied
            return {"error": str(e)}
        return {"ok": True, "hint": "call next_task for the next step"}

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
            # context-managed: this server is long-lived, so a leaked dir per
            # validate call (unlike the one-shot CLI) would pile up in /tmp
            with tempfile.TemporaryDirectory(prefix="saage-validate-") as ws:
                build_flow(path, provider=object(), workspace=ws)
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
