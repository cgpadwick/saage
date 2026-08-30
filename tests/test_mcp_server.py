"""`saage mcp` — the MCP server over the job manager. Offline: tools are
invoked in-process via MCPServer.call_tool; the launch test runs a real
command-only flow as a detached job (no LLM). POSIX-only where jobs are
launched, like the server tests."""
import os
import time

import anyio
import pytest

pytest.importorskip("mcp.server.mcpserver",
                    reason="mcp>=2 not installed (the extra, and not the 1.x SDK)")

from saage.mcp_server import build_server  # noqa: E402

posix_only = pytest.mark.skipif(os.name != "posix",
                                reason="job control needs POSIX process groups")

FLOW = ("# Echo demo flow.\n"
        "shared: {word: hello}\n"
        "workflow:\n"
        "  - {id: say, type: command, run: 'echo {{ word }} > out.txt'}\n")
BROKEN = "workflow:\n  - {id: s1, type: nope}\n"


def _call(server, tool, args):
    r = anyio.run(server.call_tool, tool, args)
    return r.structured_content, r.is_error


def _server(tmp_path, flows: dict[str, str]):
    for name, text in flows.items():
        d = tmp_path / "flows" / name
        d.mkdir(parents=True)
        (d / "flow.yaml").write_text(text)
    return build_server(flow_paths=[str(tmp_path / "flows")])


def test_tool_roster(tmp_path):
    server = _server(tmp_path, {})
    tools = anyio.run(server.list_tools)
    assert {t.name for t in tools} == {
        "list_flows", "launch_flow", "wait_for_job", "job_status", "job_logs",
        "list_jobs", "cancel_job", "validate_flow"}


def test_list_flows_reports_flows_and_breakage(tmp_path):
    server = _server(tmp_path, {"echo": FLOW, "bad": BROKEN})
    out, is_error = _call(server, "list_flows", {})
    assert not is_error
    flows = {f["name"]: f for f in out["result"]}
    assert flows["echo"]["description"] == "Echo demo flow."
    assert flows["echo"]["knobs"] == {"word": "hello"}
    assert flows["echo"]["error"] is None
    assert flows["bad"]["error"]                 # broken flow visible, not hidden


def test_launch_unknown_flow_and_bad_knob(tmp_path):
    server = _server(tmp_path, {"echo": FLOW})
    out, _ = _call(server, "launch_flow", {"flow": "nope"})
    assert "unknown flow" in out["error"] and "echo" in out["error"]
    out, _ = _call(server, "launch_flow", {"flow": "echo",
                                           "overrides": {"wat": "1"}})
    assert "wat" in out["error"]                 # unknown knob named


@posix_only
def test_launch_wait_logs_cycle(tmp_path):
    server = _server(tmp_path, {"echo": FLOW})
    out, is_error = _call(server, "launch_flow",
                          {"flow": "echo", "overrides": {"word": "bonjour"}})
    assert not is_error and "job_id" in out, out
    assert "poll" not in out["hint"].split("don't")[0]   # hint steers away from polling
    job_id = out["job_id"]

    # ONE blocking wait replaces the polling loop (the token-burn fix)
    done, _ = _call(server, "wait_for_job", {"job_id": job_id,
                                             "timeout_seconds": 30})
    assert done["status"] == "completed", done
    assert "run complete" in done["log_tail"]

    assert (tmp_path / "flows" / "echo" / "out.txt").read_text().strip() == "bonjour"
    logs, _ = _call(server, "job_logs", {"job_id": job_id})
    assert "run complete" in logs["result"]
    jobs, _ = _call(server, "list_jobs", {})
    assert any(j["job_id"] == job_id for j in jobs["result"])


@posix_only
def test_wait_for_job_timeout_returns_running(tmp_path):
    slow = ("# Sleepy flow.\nworkflow:\n"
            "  - {id: nap, type: command, run: 'sleep 5'}\n")
    server = _server(tmp_path, {"slow": slow})
    out, _ = _call(server, "launch_flow", {"flow": "slow"})
    job_id = out["job_id"]
    t0 = time.time()
    res, _ = _call(server, "wait_for_job", {"job_id": job_id,
                                            "timeout_seconds": 1})
    assert res["status"] == "running" and "again" in res["hint"]
    assert time.time() - t0 < 4                    # returned at the timeout
    _call(server, "cancel_job", {"job_id": job_id})


def test_status_logs_cancel_unknown_job(tmp_path):
    server = _server(tmp_path, {})
    out, _ = _call(server, "job_status", {"job_id": "zzz"})
    assert "unknown job" in out["error"]
    out, _ = _call(server, "wait_for_job", {"job_id": "zzz"})
    assert "unknown job" in out["error"]
    logs, _ = _call(server, "job_logs", {"job_id": "zzz"})
    assert "unknown job" in logs["result"]
    out, _ = _call(server, "cancel_job", {"job_id": "zzz"})
    assert "unknown job" in out["error"]


def test_validate_flow_tool(tmp_path):
    server = _server(tmp_path, {"echo": FLOW, "bad": BROKEN})
    out, _ = _call(server, "validate_flow",
                   {"flow_yaml": str(tmp_path / "flows" / "echo" / "flow.yaml")})
    assert out == {"ok": True}
    out, _ = _call(server, "validate_flow",
                   {"flow_yaml": str(tmp_path / "flows" / "bad" / "flow.yaml")})
    assert out["ok"] is False and "nope" in out["error"]
    out, _ = _call(server, "validate_flow", {"flow_yaml": "no/such/file.yaml"})
    assert out["ok"] is False and "not found" in out["error"]


def test_flows_edited_mid_session_are_picked_up(tmp_path):
    # agents author flows while the server is up — list_flows must see them
    server = _server(tmp_path, {})
    out, _ = _call(server, "list_flows", {})
    assert out["result"] == []
    d = tmp_path / "flows" / "fresh"
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(FLOW)
    out, _ = _call(server, "list_flows", {})
    assert [f["name"] for f in out["result"]] == ["fresh"]
