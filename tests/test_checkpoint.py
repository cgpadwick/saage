# tests/test_checkpoint.py
"""Unit tests for the checkpoint store + run registry."""
import json
import re

import pytest

from saage import checkpoint as ckpt


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / ".saage"))


def test_new_run_id_is_unique_and_sorted():
    a = ckpt.new_run_id()
    b = ckpt.new_run_id()
    assert a != b
    assert re.match(r"\d{8}-\d{6}-[0-9a-f]{8}$", a)
    assert re.match(r"\d{8}-\d{6}-[0-9a-f]{8}$", b)


def test_create_write_load_roundtrip():
    c = ckpt.Checkpoint.create("run1", flow_path="/x/flow.yaml", workspace="/ws")
    rec = c.load()
    assert rec["status"] == "running"
    assert rec["flow_path"] == "/x/flow.yaml"
    c.write({"best_score": 0.9, "_iter": {"hill": 6}}, resume_step=7, status="running")
    rec = c.load()
    assert rec["resume_step"] == 7
    assert rec["shared"]["_iter"]["hill"] == 6
    assert rec["status"] == "running"


def test_write_is_atomic_no_partial_file():
    c = ckpt.Checkpoint.create("run2")
    c.write({"k": "v"}, resume_step=0)
    # no leftover tmp file after an atomic rename
    assert not (c.dir / "checkpoint.json.tmp").exists()
    assert json.loads((c.dir / "checkpoint.json").read_text())["shared"]["k"] == "v"


def test_mark_updates_only_status():
    c = ckpt.Checkpoint.create("run3")
    c.write({"k": 1}, resume_step=2)
    c.mark("completed")
    rec = c.load()
    assert rec["status"] == "completed"
    assert rec["resume_step"] == 2          # preserved
    assert rec["shared"] == {"k": 1}        # preserved


def test_non_serializable_value_is_coerced_with_warning(caplog):
    c = ckpt.Checkpoint.create("run4")
    c.write({"obj": object()}, resume_step=0)   # not JSON-serializable
    assert "non-serializable" in caplog.text.lower()
    assert isinstance(c.load()["shared"]["obj"], str)


def test_list_runs_only_includes_dirs_with_checkpoint(tmp_path):
    ckpt.Checkpoint.create("a")
    ckpt.Checkpoint.create("b")
    (ckpt.runs_dir() / "remote_only").mkdir(parents=True)   # no checkpoint.json
    ids = {c.run_id for c in ckpt.list_runs()}
    assert ids == {"a", "b"}


def test_find_run_by_prefix_and_latest_resumable():
    a = ckpt.Checkpoint.create("20260619-100000-aaaa")
    a.write({}, resume_step=0, status="completed")
    b = ckpt.Checkpoint.create("20260619-120000-bbbb")
    b.write({}, resume_step=1, status="running")
    assert ckpt.find_run("20260619-100000-aaaa").run_id == a.run_id  # exact
    assert ckpt.find_run("20260619-1000").run_id == a.run_id         # prefix
    assert ckpt.find_run(None).run_id == b.run_id                    # latest resumable


def test_find_run_no_resumable_raises():
    a = ckpt.Checkpoint.create("only")
    a.write({}, resume_step=0, status="completed")
    with pytest.raises(FileNotFoundError):
        ckpt.find_run(None)


def test_fingerprint_changes_when_a_skill_changes(tmp_path):
    flow = tmp_path / "flow.yaml"
    flow.write_text("provider: {type: local, model: x}\nworkflow: []\n")
    skill = tmp_path / "s"
    skill.mkdir()
    (skill / "skill.md").write_text("body v1")
    fp1 = ckpt.fingerprint(flow)
    (skill / "skill.md").write_text("body v2")
    fp2 = ckpt.fingerprint(flow)
    assert fp1 != fp2
    assert fp1.startswith("sha256:")


def test_find_run_ambiguous_prefix_raises():
    ckpt.Checkpoint.create("20260619-100000-aaaa")
    ckpt.Checkpoint.create("20260619-100000-bbbb")
    with pytest.raises(FileNotFoundError, match="ambiguous"):
        ckpt.find_run("20260619-100000")
