# tests/integration/test_resume.py
"""Crash mid-loop, then resume — completed iterations are not redone."""
import pytest

from saage import checkpoint as ckpt
from saage.hydrate import build_flow


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / ".saage"))


def _loop_flow(tmp_path):
    f = tmp_path / "flow.yaml"
    f.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - id: hill\n"
        "    type: counting_loop\n"
        "    max_iterations: 5\n"
        "    body:\n"
        "      - {id: tick, type: command, run: 'echo x >> counter.txt'}\n"
    )
    return f


def test_checkpoint_written_during_run(tmp_path):
    f = _loop_flow(tmp_path)
    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    flow, seed = build_flow(f, provider=object(), workspace=str(tmp_path),
                            checkpoint=c)
    flow.run(seed)
    rec = c.load()
    # the loop is the only (index 0) top-level step
    assert rec["resume_step"] == 0
    assert rec["shared"]["_iter"]["hill"] == 5


def test_run_leaves_ledger_and_shared_json(tmp_path):
    # a run with a checkpoint sink writes a per-node ledger.jsonl + a final
    # shared.json into the run dir, for offline debugging (issue #21)
    import json
    f = _loop_flow(tmp_path)
    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    flow, seed = build_flow(f, provider=object(), workspace=str(tmp_path),
                            checkpoint=c)
    flow.run(seed)

    ledger = (c.dir / "ledger.jsonl").read_text().splitlines()
    assert ledger, "ledger.jsonl should have a line per node executed"
    rows = [json.loads(x) for x in ledger]
    assert all("node" in r and "action" in r for r in rows)
    # the loop body's `tick` command ran 5 times -> its exit code is recorded
    ticks = [r for r in rows if r["node"] == "tick"]
    assert len(ticks) == 5 and all(r["exit"] == 0 for r in ticks)

    shared = json.loads((c.dir / "shared.json").read_text())
    assert shared["_iter"]["hill"] == 5


def test_engine_stamps_completed_status_on_clean_finish(tmp_path):
    """The engine writes the terminal status in the final checkpoint write — so a
    kill after the last node (before any external mark) leaves a non-resumable
    'completed' run rather than a 'running' one that would redo the final step."""
    f = _loop_flow(tmp_path)
    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    flow, seed = build_flow(f, provider=object(), workspace=str(tmp_path),
                            checkpoint=c)
    flow.run(seed)                       # no CLI, no mark() call anywhere
    assert c.load()["status"] == "completed"


def test_resume_does_not_redo_completed_iterations(tmp_path, monkeypatch):
    import saage.nodes as nodes
    f = _loop_flow(tmp_path)
    counter = tmp_path / "counter.txt"

    # crash entering iteration 3: the 3rd run of the body command raises
    real = nodes.run_shell
    calls = {"n": 0}

    def flaky(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(cmd, **kw)

    monkeypatch.setattr(nodes, "run_shell", flaky)

    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    flow, seed = build_flow(f, provider=object(), workspace=str(tmp_path),
                            checkpoint=c)
    with pytest.raises(RuntimeError, match="boom"):
        flow.run(seed)

    assert counter.read_text().count("x") == 2          # iterations 1-2 only
    rec = c.load()
    assert rec["shared"]["_iter"]["hill"] == 2

    # --- resume: real shell back, restart at the saved step ---
    monkeypatch.setattr(nodes, "run_shell", real)
    c2 = ckpt.Checkpoint(c.run_id)
    flow2, _ = build_flow(f, provider=object(), workspace=str(tmp_path),
                          checkpoint=c2, resume_step=rec["resume_step"])
    resumed_seed = dict(rec["shared"])
    flow2.run(resumed_seed)

    # iterations 3,4,5 appended -> 5 total. 7 would mean 1-2 were redone.
    assert counter.read_text().count("x") == 5
    assert resumed_seed["_iter"]["hill"] == 5


def test_run_flow_resume_helper(tmp_path, monkeypatch):
    """run_flow(resume=ckpt) restores shared and restarts at resume_step."""
    import saage.nodes as nodes
    from saage.hydrate import run_flow
    f = _loop_flow(tmp_path)
    counter = tmp_path / "counter.txt"

    real = nodes.run_shell
    calls = {"n": 0}

    def flaky(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(cmd, **kw)

    monkeypatch.setattr(nodes, "run_shell", flaky)
    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    with pytest.raises(RuntimeError):
        run_flow(f, provider=object(), workspace=str(tmp_path), checkpoint=c)

    monkeypatch.setattr(nodes, "run_shell", real)
    out = run_flow(f, provider=object(), workspace=str(tmp_path),
                   resume=ckpt.Checkpoint(c.run_id))
    assert counter.read_text().count("x") == 5
    assert out["_iter"]["hill"] == 5


def test_resume_honors_new_workspace(tmp_path, monkeypatch):
    """A --workspace given at resume time wins over the original run's path."""
    import saage.nodes as nodes
    from saage.hydrate import run_flow
    f = _loop_flow(tmp_path)

    real = nodes.run_shell
    calls = {"n": 0}

    def flaky(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(cmd, **kw)

    monkeypatch.setattr(nodes, "run_shell", flaky)
    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    with pytest.raises(RuntimeError):
        run_flow(f, provider=object(), workspace=str(tmp_path), checkpoint=c)

    monkeypatch.setattr(nodes, "run_shell", real)
    new_ws = tmp_path / "resumed_ws"
    out = run_flow(f, provider=object(), workspace=str(new_ws),
                   resume=ckpt.Checkpoint(c.run_id))
    # the template/seed workspace reflects the resume-time dir, not the original
    assert out["workspace"] == str(new_ws.resolve())
    # and the loop still completed from where it left off
    assert out["_iter"]["hill"] == 5
