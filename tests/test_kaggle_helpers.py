"""Correctness tests for the kaggle_solver flow's deterministic helpers.

These are the gameable/critical bits — score reading, the keep-vs-revert
decision, submission validation, and grade-report extraction. Like the
greenfield helper tests, the LLM-driven flow itself is not exercised; only the
deterministic scripts are, as subprocesses (or pure-function imports) so a
silent regression in scoring/decision logic is caught.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

KAGGLE = Path(__file__).resolve().parent.parent / "flows" / "kaggle_solver"


def _run(script: str, *args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(KAGGLE / script), *args],
                          cwd=cwd, capture_output=True, text=True)


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


# ── read_val_score.py ────────────────────────────────────────────────────────

def test_read_val_score_valid(tmp_path):
    (tmp_path / "eval_results.json").write_text(
        json.dumps({"metric_name": "rmse", "value": 0.42}))
    out = _run("read_val_score.py", cwd=tmp_path).stdout
    assert "VAL_SCORE=0.42" in out
    assert "METRIC=rmse" in out


def test_read_val_score_missing_file_is_nan(tmp_path):
    out = _run("read_val_score.py", cwd=tmp_path).stdout
    assert "VAL_SCORE=nan" in out


def test_read_val_score_malformed_is_nan(tmp_path):
    (tmp_path / "eval_results.json").write_text(json.dumps({"no_value": 1}))
    assert "VAL_SCORE=nan" in _run("read_val_score.py", cwd=tmp_path).stdout


def test_read_val_score_nonnumeric_is_nan(tmp_path):
    (tmp_path / "eval_results.json").write_text(json.dumps({"value": "abc"}))
    assert "VAL_SCORE=nan" in _run("read_val_score.py", cwd=tmp_path).stdout


# ── validate_submission.py (ACTION: pass|fail) ───────────────────────────────

def _sample(tmp_path, rows=("id,target", "1,0.1", "2,0.9")):
    (tmp_path / "sample_submission.csv").write_text("\n".join(rows) + "\n")


def test_validate_submission_pass(tmp_path):
    _sample(tmp_path)
    (tmp_path / "submission.csv").write_text("id,target\n1,0.3\n2,0.7\n")
    out = _run("validate_submission.py", cwd=tmp_path).stdout
    assert out.strip().endswith("ACTION: pass")


def test_validate_submission_missing_sample_fails(tmp_path):
    (tmp_path / "submission.csv").write_text("id,target\n1,0.3\n")
    assert "ACTION: fail" in _run("validate_submission.py", cwd=tmp_path).stdout


def test_validate_submission_missing_submission_fails(tmp_path):
    _sample(tmp_path)
    assert "ACTION: fail" in _run("validate_submission.py", cwd=tmp_path).stdout


def test_validate_submission_column_mismatch_fails(tmp_path):
    _sample(tmp_path)
    (tmp_path / "submission.csv").write_text("id,WRONG\n1,0.3\n2,0.7\n")
    assert "ACTION: fail" in _run("validate_submission.py", cwd=tmp_path).stdout


def test_validate_submission_row_count_mismatch_fails(tmp_path):
    _sample(tmp_path)
    (tmp_path / "submission.csv").write_text("id,target\n1,0.3\n")   # 1 row, expect 2
    assert "ACTION: fail" in _run("validate_submission.py", cwd=tmp_path).stdout


def test_validate_submission_nan_cell_fails(tmp_path):
    _sample(tmp_path)
    (tmp_path / "submission.csv").write_text("id,target\n1,\n2,nan\n")
    assert "ACTION: fail" in _run("validate_submission.py", cwd=tmp_path).stdout


# ── keep_or_revert.py (direction-aware keep/revert) ──────────────────────────

@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "model.py").write_text("v = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    return tmp_path


def _keep(repo, **kw):
    args = []
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    return _run("keep_or_revert.py", *args, cwd=repo).stdout


def test_keep_baseline_records_first_score(repo):
    out = _keep(repo, candidate=0.5, best=-1, baseline="true")
    assert "RESULT=keep" in out and "BEST_SCORE=0.5" in out and "FAILURES=0" in out


def test_keep_when_higher_is_better_improves(repo):
    out = _keep(repo, candidate=0.6, best=0.5)
    assert "RESULT=keep" in out and "BEST_SCORE=0.6" in out


def test_revert_when_not_improved_increments_failures(repo):
    out = _keep(repo, candidate=0.4, best=0.5, failures=2)
    assert "RESULT=revert" in out and "BEST_SCORE=0.5" in out and "FAILURES=3" in out


def test_tie_reverts(repo):
    out = _keep(repo, candidate=0.5, best=0.5)
    assert "RESULT=revert" in out and "BEST_SCORE=0.5" in out


def test_lower_is_better_keeps_lower(repo):
    out = _keep(repo, candidate=0.3, best=0.5, lower_is_better="true")
    assert "RESULT=keep" in out and "BEST_SCORE=0.3" in out


def test_lower_is_better_reverts_higher(repo):
    out = _keep(repo, candidate=0.9, best=0.5, lower_is_better="true")
    assert "RESULT=revert" in out and "BEST_SCORE=0.5" in out


def test_nan_candidate_always_reverts(repo):
    out = _keep(repo, candidate="nan", best=0.5)
    assert "RESULT=revert" in out and "BEST_SCORE=0.5" in out


def test_target_met_direction_aware(repo):
    assert "TARGET_MET=1" in _keep(repo, candidate=0.8, best=-1, baseline="true", target=0.7)
    assert "TARGET_MET=0" in _keep(repo, candidate=0.5, best=-1, baseline="true", target=0.7)
    assert "TARGET_MET=1" in _keep(repo, candidate=0.2, best=999, baseline="true",
                                   target=0.3, lower_is_better="true")


def test_research_log_survives_revert(repo):
    (repo / "research_log.md").write_text("keep me\n")
    _keep(repo, candidate=0.4, best=0.5)            # worse -> revert (git clean -fd)
    assert (repo / "research_log.md").exists()
    assert "keep me" in (repo / "research_log.md").read_text()


# ── grade.py extract() (pure; mlebench-calling path needs the external tool) ──

def _grade_mod():
    spec = importlib.util.spec_from_file_location("kaggle_grade", KAGGLE / "grade.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_grade_extract_medal_and_score():
    extract = _grade_mod().extract
    assert extract({"gold_medal": True, "score": 0.9}) == ("gold", 0.9)
    assert extract({"silver_medal": True, "score": 0.8}) == ("silver", 0.8)
    assert extract({"bronze_medal": True, "score": 0.5}) == ("bronze", 0.5)
    medal, score = extract({"score": None})
    assert medal == "none" and score != score        # NaN


def test_grade_missing_submission_is_unknown_nan(tmp_path):
    r = _run("grade.py", "--comp", "x", cwd=tmp_path)   # no submission.csv
    assert "MEDAL=unknown TEST_SCORE=nan" in r.stdout
    assert r.returncode == 1
