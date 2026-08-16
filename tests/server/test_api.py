"""Tests for saage.server.app — FastAPI routes and SSE streams."""
import json
from unittest import mock

import pytest

pytest.importorskip("fastapi", reason="server extra not installed")

from fastapi.testclient import TestClient  # noqa: E402

from saage.llm import LLMResponse, ScriptedProvider  # noqa: E402
from saage.server.app import _tail, _tail_ledger, create_app  # noqa: E402
from saage.server.config import ServerConfig  # noqa: E402


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


BROKEN = "provider: {type: local, model: m}\nworkflow:\n  - {id: s1, type: nope}\n"


@pytest.fixture
def broken_flow(tmp_path):
    d = tmp_path / "flows" / "busted"
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(BROKEN)
    return d / "flow.yaml"


def test_launch_rejects_broken_flow(tmp_path, sleeper_flow, broken_flow):
    """A flow that failed hydration must not be launchable via the JSON API."""
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"flow": "busted"})
    assert r.status_code == 422 and "broken" in r.json()["detail"]


def test_launch_form_rejects_broken_flow(tmp_path, sleeper_flow, broken_flow):
    """A flow that failed hydration must not be launchable via the form bridge."""
    c = _client(tmp_path)
    r = c.post("/launch-form", data={"flow": "busted"})
    assert r.status_code == 422 and "broken" in r.json()["detail"]


def test_home_page_marks_broken_flow_disabled(tmp_path, sleeper_flow, broken_flow):
    """Broken flows appear in the dropdown but disabled, with their error."""
    c = _client(tmp_path)
    html = c.get("/").text
    assert "busted" in html
    assert "disabled" in html


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


def test_job_page_closes_log_stream_on_done(tmp_path, sleeper_flow):
    """The log EventSource must close on the ``done`` event: EventSource
    auto-reconnects after any stream end, and the server replays the log from
    offset 0 each time — without close() a finished job's log panel re-appends
    the whole log every few seconds forever."""
    c = _client(tmp_path)
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "30"}}).json()["job_id"]
    page = c.get(f"/jobs/{jid}").text
    assert "logEs.close()" in page
    # Both streams (logs + ledger) handle the terminal event.
    assert page.count('addEventListener("done"') == 2
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


# ---------------------------------------------------------------------------
# /launch-form — form-encoded knob bridge
# ---------------------------------------------------------------------------

def test_launch_form_success_redirects_to_job(tmp_path, sleeper_flow):
    """POST /launch-form with flat form fields must launch and redirect to the job page."""
    c = _client(tmp_path)
    r = c.post(
        "/launch-form",
        data={"flow": "sleeper", "overrides.seconds": "1"},
        follow_redirects=False,
    )
    # Plain (non-htmx) client: 303 + Location pointing at a job page
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/jobs/")
    jid = loc.split("/jobs/")[1]
    # The job must actually exist
    assert c.get(f"/api/jobs/{jid}").status_code == 200
    c.post(f"/api/jobs/{jid}/cancel")


def test_launch_form_htmx_returns_hx_redirect(tmp_path, sleeper_flow):
    """When htmx sends HX-Request header, endpoint returns 200 + HX-Redirect."""
    c = _client(tmp_path)
    r = c.post(
        "/launch-form",
        data={"flow": "sleeper", "overrides.seconds": "1"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Redirect" in r.headers
    assert r.headers["HX-Redirect"].startswith("/jobs/")


def test_launch_form_unknown_flow_returns_404(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/launch-form", data={"flow": "ghost"})
    assert r.status_code == 404


def test_launch_form_unknown_knob_returns_422(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/launch-form", data={"flow": "sleeper", "overrides.nope": "1"})
    assert r.status_code == 422 and "nope" in r.json()["detail"]


# ---------------------------------------------------------------------------
# /parse-form — NL confirm path and XSS escaping
# ---------------------------------------------------------------------------

def test_parse_form_confirm_uses_launch_form(tmp_path, sleeper_flow):
    """parse-form must return a <form> posting to /launch-form with hidden inputs."""
    c = _client(tmp_path, ['{"flow": "sleeper", "overrides": {"seconds": "3"}, "explanation": "quick nap"}'])
    r = c.post("/parse-form", data={"text": "sleep for 3 seconds"})
    assert r.status_code == 200
    html = r.text
    # Must use /launch-form, not /api/jobs
    assert "/launch-form" in html
    assert "/api/jobs" not in html
    # Must have hidden inputs with correct names
    assert 'name="flow"' in html
    assert 'name="overrides.seconds"' in html
    # hx-vals must not appear (no raw JSON attributes)
    assert "hx-vals" not in html


def test_parse_form_escapes_xss_in_flow_name(tmp_path, sleeper_flow):
    """LLM-returned explanation containing HTML special chars must be escaped."""
    evil_explanation = '<script>alert(1)</script>'
    reply = f'{{"flow": "sleeper", "overrides": {{}}, "explanation": "{evil_explanation}"}}'
    c = _client(tmp_path, [reply])
    r = c.post("/parse-form", data={"text": "anything"})
    html = r.text
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_parse_form_escapes_xss_in_override_value(tmp_path, sleeper_flow):
    """Override values with HTML must be escaped in the fragment."""
    evil_val = "<script>steal()</script>"
    reply = f'{{"flow": "sleeper", "overrides": {{"seconds": "{evil_val}"}}, "explanation": ""}}'
    c = _client(tmp_path, [reply])
    r = c.post("/parse-form", data={"text": "anything"})
    html = r.text
    assert "<script>steal()" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# graph-data script tag — JSON must be parseable (regression: Jinja2 autoescape)
# ---------------------------------------------------------------------------

def test_job_page_graph_data_is_valid_json(tmp_path, sleeper_flow):
    """The <script id="graph-data"> element must contain parseable JSON.

    Jinja2 HTML-autoescape converts quotes to &#34; inside script tags, which
    browsers do NOT decode (script is a raw-text element).  We use ``| tojson``,
    which emits parseable JSON with script-safe \\u003c escaping; this test
    catches any regression that re-introduces HTML entity escaping.
    """
    c = _client(tmp_path)
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "30"}}).json()["job_id"]
    html = c.get(f"/jobs/{jid}").text

    # Extract the raw text between the opening and closing tags
    marker_open = '<script type="application/json" id="graph-data">'
    marker_close = "</script>"
    start = html.index(marker_open) + len(marker_open)
    end = html.index(marker_close, start)
    raw = html[start:end]

    # Must parse cleanly (&#34; would cause json.loads to raise)
    data = json.loads(raw)

    # Must contain the expected node id from the sleeper flow
    node_ids = [n["id"] for n in data.get("nodes", [])]
    assert "nap" in node_ids, f"Expected node 'nap' in graph data; got {node_ids}"

    c.post(f"/api/jobs/{jid}/cancel")


def test_cross_origin_post_rejected(tmp_path, sleeper_flow):
    """A browser on any website can form-POST to localhost — the Origin
    check must reject state-changing requests from foreign origins."""
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"flow": "sleeper"},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    # Same-origin browser posts and header-less clients (curl) still work
    r = c.post("/api/jobs", json={"flow": "sleeper", "overrides": {"seconds": "30"}},
               headers={"Origin": "http://127.0.0.1:8321"})
    assert r.status_code == 201
    c.post(f"/api/jobs/{r.json()['job_id']}/cancel")


def test_flow_knobs_escape_names_and_defaults(tmp_path):
    """Knob names/defaults come from flow YAML — must not land raw in HTML."""
    d = tmp_path / "flows" / "evil"
    d.mkdir(parents=True)
    d.joinpath("flow.yaml").write_text(
        "provider: {type: local, model: m}\n"
        'shared: {mal: "<img src=x onerror=alert(1)>"}\n'
        "workflow:\n  - {id: a, type: command, run: 'echo {{ mal }}'}\n")
    c = _client(tmp_path)
    html = c.get("/flow-knobs", params={"flow": "evil"}).text
    assert "<img" not in html and "&lt;img" in html


def test_parse_form_has_edit_button(tmp_path, sleeper_flow):
    """The parse preview offers Confirm / Edit / Cancel (design contract)."""
    c = _client(tmp_path, replies=[
        '{"flow": "sleeper", "overrides": {"seconds": "5"}, "explanation": "ok"}'])
    html = c.post("/parse-form", data={"text": "nap for five seconds"}).text
    assert ">Confirm<" in html and ">Cancel<" in html
    assert 'saageEditParsed' in html and 'data-flow="sleeper"' in html
    assert "&#34;seconds&#34;" in html or "&quot;seconds&quot;" in html  # escaped JSON attr


def _external_run(tmp_path, job_id="20260101-000000-deadbeef", status="completed"):
    """Plant a run-store entry the server didn't launch (saage run / resume)."""
    import os
    from pathlib import Path
    run_dir = Path(os.environ["SAAGE_HOME"]) / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "checkpoint.json").write_text(json.dumps({
        "run_id": job_id, "status": status, "started_at": "2026-01-01T00:00:00Z",
        "flow_path": "/somewhere/flows/external_flow/flow.yaml"}))
    (run_dir / "run.log").write_text("external log line\n")
    (run_dir / "ledger.jsonl").write_text(
        '{"step": 0, "node": "a", "phase": "start"}\n')
    return job_id


def test_history_includes_run_store_entries(tmp_path, sleeper_flow):
    """Runs from `saage run`/`resume` (no registry entry) appear in history."""
    c = _client(tmp_path)
    jid = _external_run(tmp_path)
    html = c.get("/history").text
    assert jid[:12] in html or jid in html
    assert "external_flow" in html


def test_external_run_detail_and_streams_accessible(tmp_path, sleeper_flow):
    """Detail page, JSON API, dag.svg, and SSE streams work for run-store-only
    entries; cancel is a no-op that preserves the checkpoint status."""
    c = _client(tmp_path)
    jid = _external_run(tmp_path)
    assert c.get(f"/jobs/{jid}").status_code == 200
    body = c.get(f"/api/jobs/{jid}").json()
    assert body["status"] == "completed" and body["flow_name"] == "external_flow"
    assert c.get(f"/jobs/{jid}/dag.svg").status_code == 200
    with c.stream("GET", f"/api/jobs/{jid}/logs") as r:
        text = "".join(chunk for chunk, _ in zip(r.iter_text(), range(10)))
    assert "external log line" in text and "completed" in text
    assert c.post(f"/api/jobs/{jid}/cancel").json()["status"] == "completed"
