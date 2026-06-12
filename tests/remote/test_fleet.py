"""Coordinator-on-a-box: scoped credentials, sweep naming/teardown scoping,
the watcher's two-stage teardown, thunder CLI output parsing, and handoff
push_files validation. All offline."""
from __future__ import annotations

import pytest

from saage.remote import fleet, observe
from saage.remote.creds import Storage, Target, add_target, list_targets
from saage.remote.fleet import (BATCH_DONE_MARKER, scoped_credentials,
                                sweep_names, sweep_targets, sweep_watch)
from saage.remote.state import RunState
from saage.remote.thunder import ThunderError, parse_tnr_json

STORAGE = Storage(endpoint="https://r2", bucket="b", access_key="ak",
                  secret_key="sk")


# ---- scoped credentials ------------------------------------------------------

def test_scoped_credentials_contains_only_sweep_workers():
    workers = [Target(name="sweep-ab12-w1", host="1.2.3.4", user="ubuntu",
                      port=31872, hourly_usd=0.35, max_runs=2)]
    text = scoped_credentials(workers, "/home/ubuntu/run/saage_home/ssh/sweep_key",
                              storage=STORAGE)
    try:
        import tomllib                  # 3.11+
    except ModuleNotFoundError:
        import tomli as tomllib         # the venv's 3.10
    doc = tomllib.loads(text)
    assert set(doc["targets"]) == {"sweep-ab12-w1"}
    t = doc["targets"]["sweep-ab12-w1"]
    assert (t["host"], t["user"], t["port"]) == ("1.2.3.4", "ubuntu", 31872)
    assert t["max_runs"] == 2
    assert t["key"] == "/home/ubuntu/run/saage_home/ssh/sweep_key"
    assert doc["storage"]["bucket"] == "b"          # mirror travels
    assert "lambda" not in doc and "thundercompute" not in doc


def test_scoped_credentials_without_storage():
    text = scoped_credentials([Target(name="w", host="h")], "/k")
    assert "[storage]" not in text


# ---- naming + teardown scoping ----------------------------------------------

def test_sweep_names_shape():
    coord, workers = sweep_names("ab12", 3)
    assert coord == "sweep-ab12-c"
    assert workers == ["sweep-ab12-w1", "sweep-ab12-w2", "sweep-ab12-w3"]


def test_sweep_targets_never_sees_other_boxes(saage_home):
    add_target("lewm-best", "10.0.0.1")
    add_target("sweep-ab12-c", "10.0.0.2")
    add_target("sweep-ab12-w1", "10.0.0.3")
    add_target("sweep-ffff-w1", "10.0.0.4")
    mine = sweep_targets("ab12")
    assert set(mine) == {"sweep-ab12-c", "sweep-ab12-w1"}


def test_sweep_down_only_workers_keeps_coordinator(saage_home, monkeypatch):
    add_target("sweep-ab12-c", "10.0.0.2")
    add_target("sweep-ab12-w1", "10.0.0.3")
    add_target("sweep-ab12-w2", "10.0.0.4")
    killed = []

    class FakeBackend:
        def terminate_target(self, target):
            killed.append(target.host)
            return True

    monkeypatch.setattr(fleet, "backend_for", lambda c: FakeBackend())
    done = fleet.sweep_down("ab12", only_workers=True, clouds=("thunder",))
    assert sorted(done) == ["sweep-ab12-w1", "sweep-ab12-w2"]
    assert "10.0.0.2" not in killed
    left = list_targets()
    assert "sweep-ab12-c" in left and "sweep-ab12-w1" not in left


# ---- the watcher --------------------------------------------------------------

def _watch_run(saage_home):
    rs = RunState.create("flow-20260612-0001-aaaa")
    rs.update(phase="running", target="sweep-ab12-c", sweep_id="ab12",
              started_at="2026-06-12T00:00:00Z")
    return rs


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, dt):
        self.t += dt


def test_watch_releases_workers_at_marker_then_full_teardown(saage_home, monkeypatch, tmp_path):
    _watch_run(saage_home)
    # mirror script: rounds running -> marker appears -> final phase
    frames = iter([
        (set(), {"phase": "running"}),
        ({BATCH_DONE_MARKER}, {"phase": "running"}),
        ({BATCH_DONE_MARKER}, {"phase": "running"}),
        ({BATCH_DONE_MARKER, "submission.csv"}, {"phase": "done"}),
    ])
    current = {}

    def advance():
        names, status = next(frames)
        current["names"], current["status"] = names, status

    advance()
    downs = []
    monkeypatch.setattr(fleet, "storage_config", lambda: STORAGE)
    monkeypatch.setattr(fleet, "bucket_names", lambda s, r: current["names"])
    monkeypatch.setattr(fleet, "_status_from_bucket", lambda s, r: current["status"])
    monkeypatch.setattr(fleet, "_fetch_from_bucket",
                        lambda s, r, d: ["submission.csv"])
    monkeypatch.setattr(fleet, "sweep_down",
                        lambda sid, only_workers=False, **kw:
                        downs.append(("workers" if only_workers else "all")) or ["x"])

    clock = FakeClock()
    real_sleep = clock.sleep

    def sleep_and_advance(dt):
        real_sleep(dt)
        advance()
    clock.sleep = sleep_and_advance

    out = sweep_watch("flow-20260612-0001-aaaa", interval=5,
                      fetch_dest=tmp_path / "out", clock=clock)
    assert downs == ["workers", "all"]              # two-stage teardown
    assert out["phase"] == "done"
    assert out["workers_released_early"] is True
    assert out["fetched"] == ["submission.csv"]


def test_watch_refuses_non_sweep_runs(saage_home):
    rs = RunState.create("flow-20260612-0002-bbbb")
    rs.update(phase="running", target="spark", started_at="2026-06-12T00:00:00Z")
    with pytest.raises(fleet.FleetError, match="sweep-up"):
        sweep_watch("flow-20260612-0002-bbbb")


# ---- thunder output parsing ---------------------------------------------------

def test_parse_tnr_json_strips_human_preamble():
    out = parse_tnr_json('Fetching instances...\n[{"uuid": "x", "id": 0}]')
    assert out[0]["uuid"] == "x"
    obj = parse_tnr_json('Creating instance...\n{"uuid": "y", "key": "k"}')
    assert obj["uuid"] == "y"


def test_parse_tnr_json_no_json_raises():
    with pytest.raises(ThunderError, match="no JSON"):
        parse_tnr_json("Fetching instances...\n")


# ---- handoff push_files guard --------------------------------------------------

def test_push_files_path_guard():
    from saage.remote.handoff import HandoffError, _check_push_path
    _check_push_path("saage_home/credentials.toml")        # fine
    for bad in ("/etc/passwd", "../evil", "a/../../b", "", "  "):
        with pytest.raises(HandoffError):
            _check_push_path(bad)
