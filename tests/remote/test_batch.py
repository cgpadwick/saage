"""Phase 2: one batch round (saage.remote.batch). Offline — the dispatch
layer is the FakeOps world from test_dispatch, extended so 'fetch' delivers
scripted experiment artifacts (eval_results.json + experiment.patch)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from saage.remote.batch import run_round, stage_job_flow
from saage.remote.creds import Target

from .test_dispatch import FakeClock, FakeOps


class BatchOps(FakeOps):
    """fetch() writes each job's artifacts; scores scripted per job name."""

    def __init__(self, clock, scores: dict, *, no_patch=(), **kw):
        super().__init__(clock, **kw)
        self.scores = scores            # job name -> float | None (no file)
        self.no_patch = set(no_patch)
        self.staged_flows = []

    def handoff(self, flow, target, set_args, env, **kw):
        self.staged_flows.append(flow)
        return super().handoff(flow, target, set_args, env, **kw)

    def fetch(self, rid, dest):
        super().fetch(rid, dest)
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        name = dest.name                            # results/<job name>
        score = self.scores.get(name)
        if score is not None:
            (dest / "eval_results.json").write_text(
                json.dumps({"metric_name": "val_acc", "value": score}))
        if name not in self.no_patch:
            (dest / "experiment.patch").write_text(f"--- fake patch {name}\n")


@pytest.fixture
def flow_dir(tmp_path):
    src = tmp_path / "flow_src"
    (src / "implement").mkdir(parents=True)
    (src / "implement" / "skill.md").write_text("# implement\n")
    (src / "experiment.yaml").write_text(yaml.safe_dump({
        "provider": {"type": "local", "model": "x"},
        "workspace": "/wrong/place",
        "workflow": [{"id": "s", "type": "command", "run": "true"}],
    }))
    return src


def _round(tmp_path, flow_dir, ops, k=3, **kw):
    props = []
    for i in range(k):
        p = tmp_path / f"prop{i}.md"
        p.write_text(f"# proposal {i}\n")
        props.append(p)
    kw.setdefault("poll_interval", 60)
    kw.setdefault("clock", ops.clock)
    return run_round(
        flow_dir / "experiment.yaml", props,
        [Target(name="a", host="a.host", max_runs=2),
         Target(name="b", host="b.host", max_runs=2)],
        workspace=tmp_path / "ws", results_dir=tmp_path / "results",
        ops=ops, **kw)


def test_staging_copies_flow_writes_proposal_and_pins_workspace(tmp_path, flow_dir):
    prop = tmp_path / "p.md"
    prop.write_text("try dropout\n")
    staged = stage_job_flow(flow_dir / "experiment.yaml", prop,
                            tmp_path / "ws", tmp_path / "stage", 0)
    assert (staged.parent / "proposal.md").read_text() == "try dropout\n"
    assert (staged.parent / "implement" / "skill.md").exists()
    doc = yaml.safe_load(staged.read_text())
    assert doc["workspace"] == str(tmp_path / "ws")
    assert doc["workflow"]                       # rest of the doc survived


def test_round_picks_highest_score(tmp_path, flow_dir):
    clock = FakeClock()
    ops = BatchOps(clock, {"p0": 0.88, "p1": 0.93, "p2": 0.91})
    s = _round(tmp_path, flow_dir, ops)
    assert (s["ok"], s["failed"]) == (3, 0)
    assert s["best_index"] == 1
    assert s["best_score"] == 0.93
    assert s["best_patch"].endswith("p1/experiment.patch")
    assert (tmp_path / "results" / "summary.json").exists()


def test_round_lower_is_better(tmp_path, flow_dir):
    clock = FakeClock()
    ops = BatchOps(clock, {"p0": 0.41, "p1": 0.36, "p2": 0.52})
    s = _round(tmp_path, flow_dir, ops, lower_is_better=True)
    assert s["best_index"] == 1


def test_failed_job_scores_nan_and_cannot_win(tmp_path, flow_dir):
    clock = FakeClock()
    # every job that lands on box 'a' crashes; box 'b' completes normally
    ops = BatchOps(clock, {"p0": 0.85, "p1": 0.83, "p2": 0.80},
                   phase_for=lambda t, n: "failed" if t == "a" else (
                       "running" if n == 1 else "done"))
    s = _round(tmp_path, flow_dir, ops)
    failed = [p for p in s["proposals"] if p["status"] != "done"]
    done = [p for p in s["proposals"] if p["status"] == "done"]
    assert failed and done
    assert all(p["score"] is None for p in failed)        # nan -> null in json
    assert s["best_index"] in [p["index"] for p in done]


def test_missing_patch_cannot_win_even_with_best_score(tmp_path, flow_dir):
    clock = FakeClock()
    ops = BatchOps(clock, {"p0": 0.99, "p1": 0.90, "p2": 0.85},
                   no_patch=("p0",))
    s = _round(tmp_path, flow_dir, ops)
    assert s["best_index"] == 1                  # 0.99 had no patch artifact


def test_all_failed_round_has_no_winner(tmp_path, flow_dir):
    clock = FakeClock()
    ops = BatchOps(clock, {}, phase_for=lambda t, n: "failed")
    s = _round(tmp_path, flow_dir, ops)
    assert s["best_index"] is None
    assert math.isnan(s["best_score"])
    assert s["ok"] == 0


def test_each_job_ships_its_own_staged_flow(tmp_path, flow_dir):
    clock = FakeClock()
    ops = BatchOps(clock, {"p0": 0.1, "p1": 0.2, "p2": 0.3})
    _round(tmp_path, flow_dir, ops)
    assert len(set(ops.staged_flows)) == 3       # three distinct flow files
    assert all("job" in f for f in ops.staged_flows)


def test_job_index_passed_via_set(tmp_path, flow_dir):
    clock = FakeClock()
    ops = BatchOps(clock, {"p0": 0.1, "p1": 0.2, "p2": 0.3})
    _round(tmp_path, flow_dir, ops, set_args={"short_epochs": "4"})
    indices = sorted(s["job_index"] for _, s in ops.handoffs)
    assert indices == ["0", "1", "2"]
    assert all(s["short_epochs"] == "4" for _, s in ops.handoffs)
