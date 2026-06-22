"""Unit tests for the greenfield hill-climb's deterministic keep/revert helper.

Runs the real script as a subprocess inside a throwaway git repo (no LLM): the
only thing under test is the deterministic keep-vs-revert decision, the git
commit/checkout/clean side effects, the failure counter, and that the research
log survives a revert (git clean would otherwise wipe it).
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "flows" / "greenfield_ml" / "keep_or_revert.py")


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


def _run(repo, candidate, best, failures=0, lower_is_better="false"):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--candidate", str(candidate),
         "--best", str(best), "--failures", str(failures),
         "--lower-is-better", lower_is_better],
        cwd=repo, capture_output=True, text=True, check=True)
    # parse the `RESULT=.. BEST_SCORE=.. FAILURES=..` line back into a dict
    return dict(tok.split("=", 1) for tok in r.stdout.split() if "=" in tok)


def test_improved_keeps_and_commits(repo):
    (repo / "model.py").write_text("v = 2\n")          # a candidate change
    out = _run(repo, candidate=0.9, best=0.8, failures=2)
    assert out == {"RESULT": "keep", "BEST_SCORE": "0.9", "FAILURES": "0"}
    # the candidate change is committed -> model.py no longer shows as dirty
    # (research_log.md is appended after the commit, so it stays untracked).
    porcelain = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                               capture_output=True, text=True).stdout
    assert "model.py" not in porcelain
    assert (repo / "model.py").read_text() == "v = 2\n"


def test_not_improved_reverts_and_counts(repo):
    (repo / "model.py").write_text("v = 999\n")         # a regression
    (repo / "junk.py").write_text("garbage\n")          # untracked candidate file
    out = _run(repo, candidate=0.7, best=0.8, failures=2)
    assert out == {"RESULT": "revert", "BEST_SCORE": "0.8", "FAILURES": "3"}
    assert (repo / "model.py").read_text() == "v = 1\n"  # reverted to baseline
    assert not (repo / "junk.py").exists()               # git clean removed it


def test_tie_is_not_improved(repo):
    (repo / "model.py").write_text("v = 2\n")
    out = _run(repo, candidate=0.8, best=0.8)
    assert out["RESULT"] == "revert"                     # equal score -> revert
    assert out["FAILURES"] == "1"


def test_lower_is_better_keeps_on_decrease(repo):
    (repo / "model.py").write_text("v = 2\n")
    out = _run(repo, candidate=0.1, best=0.2, lower_is_better="true")
    assert out == {"RESULT": "keep", "BEST_SCORE": "0.1", "FAILURES": "0"}


def test_research_log_survives_revert(repo):
    (repo / "research_log.md").write_text("- earlier history\n")  # untracked
    (repo / "model.py").write_text("v = 3\n")
    _run(repo, candidate=0.5, best=0.9)                  # not improved -> revert
    log = (repo / "research_log.md").read_text()
    assert "- earlier history" in log                    # preserved across git clean
    assert "reverted" in log                             # plus this round's entry


# ---- ledger anchoring: the record must reflect the ACTUAL change (commit_sha /
# parent_step / files_changed), not just the proposal (the lewm/MLE-beast bug) ----

import json


def _last_experiment(repo):
    rows = [json.loads(l) for l in (repo / "experiments.jsonl").read_text().splitlines() if l.strip()]
    return rows[-1]


def _head_sha(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


def test_keep_records_commit_sha_and_files(repo):
    (repo / "model.py").write_text("v = 2\n")
    _run(repo, candidate=0.9, best=0.8)
    rec = _last_experiment(repo)
    assert rec["kept"] is True
    assert rec["commit_sha"] == _head_sha(repo)          # anchored to the real commit
    assert "model.py" in rec["files_changed"]            # the actual change is recorded
    assert rec["step"] == 1 and rec["parent_step"] == 0


def test_revert_records_files_but_null_sha(repo):
    (repo / "model.py").write_text("v = 999\n")
    (repo / "extra.py").write_text("x = 1\n")            # untracked candidate file
    _run(repo, candidate=0.7, best=0.8)                  # revert
    rec = _last_experiment(repo)
    assert rec["kept"] is False
    assert rec["commit_sha"] is None                     # nothing committed on revert
    # what the failed experiment TRIED is still recorded
    assert "model.py" in rec["files_changed"] and "extra.py" in rec["files_changed"]


def test_files_changed_excludes_bookkeeping(repo):
    (repo / "model.py").write_text("v = 2\n")
    (repo / "research_log.md").write_text("- prior\n")    # untracked bookkeeping
    (repo / "eval_results.json").write_text('{"value": 0.9}\n')  # eval output, not a change
    _run(repo, candidate=0.9, best=0.8)
    rec = _last_experiment(repo)
    assert "research_log.md" not in rec["files_changed"]
    assert "experiments.jsonl" not in rec["files_changed"]
    assert "eval_results.json" not in rec["files_changed"]


# ---- research_log is the proposer's working memory: it carries the one-paragraph
# SUMMARY + outcome (terse, re-read every iteration); the FULL proposal goes only to
# experiments.jsonl for the human report ----


def test_research_log_has_summary_and_full_proposal_goes_to_jsonl(repo):
    (repo / "proposals").mkdir()
    (repo / "proposals" / "latest.md").write_text(
        "HYPOTHESIS: widen the classifier head.\nCHANGE: hidden 128 -> 256.\n"
        "RATIONALE: lots and lots of detail that should NOT bloat the log.\n")
    (repo / "proposals" / "summary.md").write_text(
        "Widen the classifier head 128->256 to add capacity.")
    (repo / "model.py").write_text("v = 2\n")
    _run(repo, candidate=0.9, best=0.8)                  # keep
    log = (repo / "research_log.md").read_text()
    assert "## Experiment 1" in log and "KEPT" in log
    assert "Widen the classifier head 128->256" in log   # the SUMMARY is in the log
    assert "model.py" in log                             # the actual change
    assert "RATIONALE: lots and lots" not in log         # full proposal NOT in the log
    rec = _last_experiment(repo)
    assert "RATIONALE: lots and lots" in rec["proposal"]  # full proposal -> jsonl
    assert rec["summary"] == "Widen the classifier head 128->256 to add capacity."


def test_summary_recorded_on_revert(repo):
    # proposals/ is untracked, so the revert's `git clean` wipes it — the summary
    # must be captured BEFORE the revert so a failed idea is still in the log
    (repo / "proposals").mkdir()
    (repo / "proposals" / "summary.md").write_text("Try lr=0.5 (too high) on SGD.")
    (repo / "model.py").write_text("v = 9\n")
    _run(repo, candidate=0.3, best=0.9)                  # revert
    log = (repo / "research_log.md").read_text()
    assert "reverted" in log
    assert "Try lr=0.5 (too high)" in log               # the failed idea's summary is recorded
    assert not (repo / "proposals" / "summary.md").exists()  # git clean wiped it


# ---- nan candidate sentinel: a crashed train/eval (the flow seeds candidate=nan)
# must REVERT in both metric directions and record a null score, never beat best ----


def test_nan_candidate_reverts_and_records_null(repo):
    (repo / "model.py").write_text("v = 2\n")
    out = _run(repo, candidate="nan", best=0.9)          # higher-is-better
    assert out["RESULT"] == "revert"
    rec = _last_experiment(repo)
    assert rec["kept"] is False and rec["candidate"] is None


def test_nan_candidate_reverts_lower_is_better(repo):
    (repo / "model.py").write_text("v = 2\n")
    out = _run(repo, candidate="nan", best=0.2, lower_is_better="true")
    assert out["RESULT"] == "revert"                     # nan must not "win" either direction


def test_parent_step_points_to_last_kept(repo):
    (repo / "model.py").write_text("v = 2\n")
    _run(repo, candidate=0.9, best=0.8)                  # step 1: keep
    (repo / "model.py").write_text("v = 3\n")
    _run(repo, candidate=0.85, best=0.9)                 # step 2: revert
    (repo / "model.py").write_text("v = 4\n")
    _run(repo, candidate=0.95, best=0.9)                 # step 3: keep
    rows = [json.loads(l) for l in (repo / "experiments.jsonl").read_text().splitlines() if l.strip()]
    assert [r["step"] for r in rows] == [1, 2, 3]
    assert rows[1]["parent_step"] == 1                   # revert branched off the kept step 1
    assert rows[2]["parent_step"] == 1                   # step 2 reverted, so parent is still step 1
