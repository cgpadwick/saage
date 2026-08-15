"""Tests for saage.server.app — FastAPI routes and SSE streams."""
import json
from unittest import mock

import pytest

from fastapi.testclient import TestClient

from saage.llm import LLMResponse, ScriptedProvider
from saage.server.app import _tail, _tail_ledger, create_app
from saage.server.config import ServerConfig


def _client(tmp_path, replies=()):
    cfg = ServerConfig(flow_paths=[tmp_path / "flows"])
    script = [LLMResponse(text=r) if isinstance(r, str) else r for r in replies]
    return TestClient(create_app(cfg, provider=ScriptedProvider(script)))


def test_flows_endpoint_lists_catalog(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    flows = c.get("/api/flows").json()
    assert flows[0]["name"] == "sleeper" and "seconds" in flows[0]["knobs"]


def test_launch_status_cancel(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"flow": "sleeper", "overrides": {"seconds": "30"}})
    assert r.status_code == 201
    jid = r.json()["job_id"]
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "running"
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200


def test_launch_rejects_unknown_knob(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"flow": "sleeper", "overrides": {"nope": "1"}})
    assert r.status_code == 422 and "nope" in r.json()["detail"]


def test_parse_endpoint_round_trip(tmp_path, sleeper_flow):
    c = _client(tmp_path, ['{"flow": "sleeper", "overrides": {"seconds": "5"},'
                           ' "explanation": "short nap"}'])
    out = c.post("/api/parse", json={"text": "nap for five seconds"}).json()
    assert out["ok"] and out["flow"] == "sleeper"


def test_logs_sse_streams_and_finishes(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "1"}}).json()["job_id"]
    with c.stream("GET", f"/api/jobs/{jid}/logs") as r:
        body = "".join(r.iter_text())
    assert "event: done" in body


def test_launch_unknown_flow_returns_404(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"flow": "ghost"})
    assert r.status_code == 404


def test_jobs_list(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"flow": "sleeper", "overrides": {"seconds": "30"}})
    jid = r.json()["job_id"]
    jobs = c.get("/api/jobs").json()
    assert any(j["job_id"] == jid for j in jobs)
    c.post(f"/api/jobs/{jid}/cancel")


def test_job_not_found_returns_404(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    assert c.get("/api/jobs/nonexistent").status_code == 404


def test_flows_refresh(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/api/flows/refresh")
    assert r.status_code == 200
    assert r.json()["count"] >= 1


# ---------------------------------------------------------------------------
# Ledger SSE — previously untested endpoint
# ---------------------------------------------------------------------------

def test_ledger_sse_streams_and_finishes(tmp_path, sleeper_flow):
    """Ledger SSE must terminate with ``event: done`` even when ledger is empty."""
    c = _client(tmp_path)
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "1"}}).json()["job_id"]
    with c.stream("GET", f"/api/jobs/{jid}/ledger") as r:
        body = "".join(r.iter_text())
    assert "event: done" in body


def test_ledger_sse_replays_completed_job(tmp_path, sleeper_flow):
    """Re-streaming a finished job's ledger replays records and closes with event: done."""
    c = _client(tmp_path)
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "1"}}).json()["job_id"]
    # First stream: waits for the job to finish
    with c.stream("GET", f"/api/jobs/{jid}/ledger") as r:
        "".join(r.iter_text())
    # Second stream: job already done — must replay instantly and close
    with c.stream("GET", f"/api/jobs/{jid}/ledger") as r2:
        full = "".join(r2.iter_text())
    assert "event: done" in full


# ---------------------------------------------------------------------------
# _tail terminal drain — unit test (no subprocess required)
# ---------------------------------------------------------------------------

def test_tail_terminal_drain_emits_final_bytes(tmp_path):
    """Bytes written between the last poll and terminal-status detection must appear."""
    log_file = tmp_path / "run.log"
    log_file.write_bytes(b"first chunk\n")

    call_count = 0

    def job_status():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "running"
        # Simulate bytes written just before the process exits
        with open(log_file, "ab") as f:
            f.write(b"final line\n")
        return "done"

    with mock.patch("saage.server.app.time.sleep"):
        events = list(_tail(log_file, job_status))

    body = "".join(events)
    assert "final line" in body
    assert "event: done" in body


# ---------------------------------------------------------------------------
# _tail_ledger partial-line safety — unit test
# ---------------------------------------------------------------------------

def test_tail_ledger_does_not_skip_partial_lines(tmp_path):
    """A record split across two writes must not be skipped permanently."""
    ledger = tmp_path / "ledger.jsonl"

    # Write the first half of a JSON line (no trailing newline yet)
    ledger.write_bytes(b'{"node":"a","phase"')

    yielded = []
    call_count = 0

    def job_status():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Complete the partial line plus a full second line
            with open(ledger, "ab") as f:
                f.write(b':"start"}\n{"node":"a","phase":"end"}\n')
            return "running"
        return "done"

    with mock.patch("saage.server.app.time.sleep"):
        events = list(_tail_ledger(ledger, job_status))

    data_events = [e for e in events if e.startswith("data:") and "done" not in e]
    assert len(data_events) == 2, f"Expected 2 ledger records, got: {events}"
    assert "event: done" in "".join(events)


# ---------------------------------------------------------------------------
# Page smoke tests
# ---------------------------------------------------------------------------

def test_pages_render(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    assert "sleeper" in c.get("/").text                  # dropdown + knob form
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "1"}}).json()["job_id"]
    page = c.get(f"/jobs/{jid}").text
    assert "dag.svg" in page and "EventSource" in page
    svg = c.get(f"/jobs/{jid}/dag.svg")
    assert svg.headers["content-type"].startswith("image/svg")
    assert 'id="node-nap"' in svg.text
    assert c.get("/history").status_code == 200
    c.post(f"/api/jobs/{jid}/cancel")


def test_home_page_has_htmx(tmp_path, sleeper_flow):
    """Home page must reference htmx and the flow selector."""
    c = _client(tmp_path)
    text = c.get("/").text
    assert "htmx" in text
    assert "flow-select" in text


def test_flow_knobs_fragment(tmp_path, sleeper_flow):
    """Flow-knobs endpoint returns knob inputs for a valid flow."""
    c = _client(tmp_path)
    r = c.get("/flow-knobs", params={"flow": "sleeper"})
    assert r.status_code == 200
    assert "seconds" in r.text


def test_flow_knobs_unknown_flow_returns_empty(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.get("/flow-knobs", params={"flow": "ghost"})
    assert r.status_code == 200
    assert r.text == ""


def test_jobs_table_fragment(tmp_path, sleeper_flow):
    """Jobs-table endpoint returns HTML with a job row after launching."""
    c = _client(tmp_path)
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "30"}}).json()["job_id"]
    r = c.get("/jobs-table")
    assert r.status_code == 200
    assert jid[:12] in r.text
    c.post(f"/api/jobs/{jid}/cancel")


def test_history_page(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "30"}}).json()["job_id"]
    r = c.get("/history")
    assert r.status_code == 200
    assert jid[:12] in r.text
    c.post(f"/api/jobs/{jid}/cancel")


def test_dag_svg_not_found(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    assert c.get("/jobs/nonexistent/dag.svg").status_code == 404


def test_job_detail_not_found(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    assert c.get("/jobs/nonexistent").status_code == 404

