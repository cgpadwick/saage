"""H3: the programmatic poll/fetch/kill surface a dispatcher builds on.
Offline — bucket and ssh are monkeypatched at the observe-module seams."""
from __future__ import annotations

import pytest

from saage.remote import observe
from saage.remote.creds import Storage, Target, add_target
from saage.remote.observe import fetch_run, kill_run, poll_run
from saage.remote.state import RunState

STORAGE = Storage(endpoint="https://r2", bucket="b", access_key="k", secret_key="s")


@pytest.fixture
def run(saage_home):
    add_target("spark", "spark.local")
    rs = RunState.create("flow-20260611-0000-aaaa")
    rs.update(phase="running", target="spark")
    return rs


class FakeNode:
    """Stands in for SshTarget: scripted status / stop recording."""
    def __init__(self, status=None, reachable=True):
        self._status = status or {}
        self.reachable = reachable
        self.stopped = []

    def read_status(self, run_id):
        if not self.reachable:
            raise RuntimeError("ssh: no route to host")
        return dict(self._status)

    def stop(self, run_id):
        self.stopped.append(run_id)


def _wire(monkeypatch, *, node=None, bucket_status=None, storage=STORAGE):
    monkeypatch.setattr(observe, "_node_for", lambda rs: node or FakeNode())
    monkeypatch.setattr(observe, "storage_config", lambda: storage)
    monkeypatch.setattr(observe, "_status_from_bucket",
                        lambda st, rid: dict(bucket_status or {}))


# ---- poll_run ---------------------------------------------------------------

def test_poll_prefers_bucket(run, monkeypatch):
    _wire(monkeypatch, node=FakeNode({"phase": "running"}),
          bucket_status={"phase": "running", "updated": "2026-06-11T00:00:00Z"})
    got = poll_run(run)
    assert got["source"] == "bucket"
    assert got["phase"] == "running"


def test_poll_falls_back_to_node_when_bucket_empty(run, monkeypatch):
    _wire(monkeypatch, node=FakeNode({"phase": "running"}), bucket_status={})
    assert poll_run(run)["source"] == "node"


def test_poll_no_storage_no_node_reports_local_intent(run, monkeypatch):
    _wire(monkeypatch, node=FakeNode(reachable=False), storage=None)
    got = poll_run(run)
    assert got["source"] == "local"
    assert got["phase"] == "running"        # the recorded intent, clearly labeled


def test_poll_folds_final_phase_into_local_state(run, monkeypatch):
    _wire(monkeypatch, bucket_status={"phase": "done"})
    assert poll_run(run)["phase"] == "done"
    assert run.state()["phase"] == "done"
    assert any(e["event"] == "phase_from_node" for e in run.events())


def test_poll_node_first_when_asked(run, monkeypatch):
    _wire(monkeypatch, node=FakeNode({"phase": "running"}),
          bucket_status={"phase": "running"})
    assert poll_run(run, prefer="node")["source"] == "node"


def test_poll_dead_box_stale_mirror_still_answers(run, monkeypatch):
    # the box is gone; the mirror's last heartbeat is the only truth left
    _wire(monkeypatch, node=FakeNode(reachable=False),
          bucket_status={"phase": "running", "updated": "2026-06-11T00:00:00Z"})
    got = poll_run(run)
    assert (got["source"], got["phase"]) == ("bucket", "running")


# ---- fetch_run / kill_run ----------------------------------------------------

def test_fetch_falls_back_to_bucket_when_node_gone(run, monkeypatch, tmp_path):
    _wire(monkeypatch, storage=STORAGE)

    class GoneNode(FakeNode):
        run_dir = staticmethod(lambda rid: f".saage_runs/{rid}")

        @property
        def conn(self):
            raise RuntimeError("no route to host")

    pulled = []
    monkeypatch.setattr(observe, "_node_for", lambda rs: GoneNode())
    monkeypatch.setattr(observe, "_fetch_from_bucket",
                        lambda st, rid, out: pulled.append(rid) or ["a.csv"])
    dest, files, source = fetch_run(run, tmp_path / "out")
    assert source == "bucket mirror"
    assert pulled == [run.run_id]
    assert any(e["event"] == "fetched" for e in run.events())


def test_kill_run_stops_and_records(run, monkeypatch):
    node = FakeNode()
    _wire(monkeypatch, node=node)
    kill_run(run)
    assert node.stopped == [run.run_id]
    assert run.state()["phase"] == "killed"
