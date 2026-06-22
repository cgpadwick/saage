"""Unit tests for the kaggle_solver hill-climb keep/revert helper (offline).

Runs the real script as a subprocess in a throwaway git repo (no LLM). Mirrors
tests/test_keep_or_revert.py but for kaggle's arg shape (--baseline/--target,
nan sentinels) and its generated-output set (submission.csv, training.log).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "flows" / "kaggle_solver" / "keep_or_revert.py")


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A git repo with one committed file = the 'last kept' baseline. Mirrors
    production by gitignoring experiments.jsonl so it survives `git clean` on a
    revert (the ledger must accumulate across the run)."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "model.py").write_text("v = 1\n")
    (tmp_path / ".gitignore").write_text("experiments.jsonl\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    return tmp_path


def _run(repo, candidate, best, failures=0, lower_is_better="false",
         target="", baseline="false"):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--candidate", str(candidate),
         "--best", str(best), "--failures", str(failures),
         "--lower-is-better", lower_is_better, "--target", target,
         "--baseline", baseline],
        cwd=repo, capture_output=True, text=True, check=True)
    return dict(tok.split("=", 1) for tok in r.stdout.split() if "=" in tok)


def _last_experiment(repo):
    rows = [json.loads(l) for l in (repo / "experiments.jsonl").read_text().splitlines() if l.strip()]
    return rows[-1]


def _head_sha(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


def test_keep_records_commit_sha_parent_and_files(repo):
    (repo / "model.py").write_text("v = 2\n")
    _run(repo, candidate=0.9, best=0.8)                   # higher-is-better keep
    rec = _last_experiment(repo)
    assert rec["kept"] is True
    assert rec["commit_sha"] == _head_sha(repo)
    assert "model.py" in rec["files_changed"]
    assert rec["step"] == 1 and rec["parent_step"] == 0


def test_revert_records_files_but_null_sha(repo):
    (repo / "model.py").write_text("v = 999\n")
    (repo / "extra.py").write_text("x = 1\n")             # untracked candidate file
    _run(repo, candidate=0.7, best=0.8)                   # not improved -> revert
    rec = _last_experiment(repo)
    assert rec["kept"] is False
    assert rec["commit_sha"] is None
    assert "model.py" in rec["files_changed"] and "extra.py" in rec["files_changed"]


def test_files_changed_excludes_bookkeeping_and_outputs(repo):
    (repo / "model.py").write_text("v = 2\n")
    (repo / "research_log.md").write_text("- prior\n")
    (repo / "eval_results.json").write_text('{"value": 0.9}\n')
    (repo / "submission.csv").write_text("id,target\n1,0\n")
    (repo / "training.log").write_text("epoch 1\n")
    _run(repo, candidate=0.9, best=0.8)
    rec = _last_experiment(repo)
    for noise in ("research_log.md", "experiments.jsonl", "eval_results.json",
                  "submission.csv", "training.log"):
        assert noise not in rec["files_changed"]


def test_research_log_has_summary_full_proposal_in_jsonl(repo):
    (repo / "proposals").mkdir()
    (repo / "proposals" / "latest.md").write_text(
        "HYPOTHESIS: add TF-IDF features.\nCHANGE: ngram_range (1,1)->(1,2).\n"
        "RATIONALE: lots of detail that must NOT bloat the log.\n")
    (repo / "proposals" / "summary.md").write_text(
        "Add bigram TF-IDF features (ngram_range 1->2) to capture word pairs.")
    (repo / "model.py").write_text("v = 2\n")
    _run(repo, candidate=0.9, best=0.8)
    log = (repo / "research_log.md").read_text()
    assert "## Experiment 1" in log and "KEPT" in log
    assert "Add bigram TF-IDF features" in log
    assert "model.py" in log
    assert "RATIONALE: lots of detail" not in log
    rec = _last_experiment(repo)
    assert "RATIONALE: lots of detail" in rec["proposal"]
    assert rec["summary"] == "Add bigram TF-IDF features (ngram_range 1->2) to capture word pairs."


def test_summary_recorded_on_revert(repo):
    (repo / "proposals").mkdir()
    (repo / "proposals" / "summary.md").write_text("Try LR=0.5 (too high).")
    (repo / "model.py").write_text("v = 9\n")
    _run(repo, candidate=0.3, best=0.9)                   # revert
    log = (repo / "research_log.md").read_text()
    assert "reverted" in log and "Try LR=0.5 (too high)" in log
    assert not (repo / "proposals" / "summary.md").exists()  # git clean wiped it


def test_baseline_parent_zero_and_has_sha(repo):
    (repo / "model.py").write_text("v = 2\n")
    out = _run(repo, candidate=0.8, best="nan", baseline="true")
    assert out["RESULT"] == "keep"
    rec = _last_experiment(repo)
    assert rec["kept"] is True and rec["parent_step"] == 0
    assert rec["commit_sha"] == _head_sha(repo)
