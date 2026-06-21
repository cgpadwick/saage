"""setup_competition.py resets the ledger per run (offline).

A reused kaggle workspace must not carry a prior run's research_log/experiments
into this run (the non-monotonic-best bug). Setup runs once per fresh run.
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "flows" / "kaggle_solver" / "setup_competition.py")


def _run(repo):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--comp", "demo-comp",
         "--metric", "accuracy", "--lower-is-better", "false",
         "--short-epochs", "15", "--final-epochs", "100", "--branch", "saage-kaggle"],
        cwd=repo, capture_output=True, text=True)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "task.md").write_text("# demo competition\n")
    return tmp_path


def test_setup_resets_stale_ledger(workspace):
    # simulate a prior run's leftovers in a reused workspace
    (workspace / "research_log.md").write_text("STALE prior-run content\n")
    (workspace / "experiments.jsonl").write_text(
        '{"step": 1, "best": 99}\n{"step": 2, "best": 98}\n')
    r = _run(workspace)
    assert r.returncode == 0, r.stderr
    log = (workspace / "research_log.md").read_text()
    assert "STALE prior-run content" not in log          # reset, fresh header
    assert "kaggle solver research log" in log
    assert not (workspace / "experiments.jsonl").exists()  # wiped for the new run
