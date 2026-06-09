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
    """A git repo with one committed file = the 'last kept' baseline."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "model.py").write_text("v = 1\n")
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
    assert "-> revert" in log                            # plus this round's entry
