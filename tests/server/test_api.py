"""Tests for saage.server.app — FastAPI routes and SSE streams."""
import pytest

from fastapi.testclient import TestClient

from saage.llm import LLMResponse, ScriptedProvider
from saage.server.app import create_app
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
