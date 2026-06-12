"""Unit tests for the fmnist_batch round integrator (deterministic, no LLM):
apply-if-improved, failure counting, target detection, ledger writes."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "flows" / "fmnist_batch" / "integrate.py")


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def ws(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "train.py").write_text("lr = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    return tmp_path


def _patch_for(ws, new_content: str) -> Path:
    (ws / "train.py").write_text(new_content)
    diff = subprocess.run(["git", "diff", "--binary"], cwd=ws,
                          capture_output=True, text=True).stdout
    subprocess.run(["git", "checkout", "--", "train.py"], cwd=ws, check=True,
                   capture_output=True)
    p = ws / "winner.patch"
    p.write_text(diff)
    return p


def _summary(ws, best_index=0, scores=(0.9, 0.85, None)) -> Path:
    props = []
    for i, s in enumerate(scores):
        pf = ws / f"prop{i}.md"
        pf.write_text(f"# idea {i}\n")
        props.append({"index": i, "proposal": str(pf), "score": s,
                      "status": "done" if s is not None else "failed"})
    out = ws / "summary.json"
    out.write_text(json.dumps({"proposals": props, "best_index": best_index}))
    return out


def _run(ws, **kv):
    defaults = {"candidate": "0.9", "best": "0.85", "patch": "",
                "summary": str(ws / "summary.json"), "target": "0.95",
                "failures": "0", "round": "1"}
    defaults.update({k.replace("_", "-"): v for k, v in kv.items()})
    argv = [sys.executable, str(SCRIPT)]
    for k, v in defaults.items():
        argv += [f"--{k}", str(v)]
    proc = subprocess.run(argv, cwd=ws, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines()
                if "=" in line and line.split("=")[0].isupper())


def test_improvement_applies_patch_and_commits(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    out = _run(ws, candidate="0.9", best="0.85", patch=str(patch))
    assert out["BEST_SCORE"] == "0.9"
    assert out["FAILURES"] == "0"
    assert out["TARGET_MET"] == "0"
    assert out["ROUND_NO"] == "2"
    assert (ws / "train.py").read_text() == "lr = 2\n"
    log = subprocess.run(["git", "log", "--oneline"], cwd=ws,
                         capture_output=True, text=True).stdout
    assert "round 1" in log and "idea 0" in log


def test_no_improvement_keeps_best_and_counts_failure(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    out = _run(ws, candidate="0.80", best="0.85", patch=str(patch))
    assert out["BEST_SCORE"] == "0.85"
    assert out["FAILURES"] == "1"
    assert (ws / "train.py").read_text() == "lr = 1\n"      # untouched


def test_nan_candidate_is_a_failed_round(ws):
    _summary(ws, scores=(None, None, None))
    out = _run(ws, candidate="nan", best="0.85", failures="1")
    assert out["BEST_SCORE"] == "0.85"
    assert out["FAILURES"] == "2"


def test_first_round_beats_nan_best(ws):
    patch = _patch_for(ws, "lr = 3\n")
    _summary(ws)
    out = _run(ws, candidate="0.88", best="nan", patch=str(patch))
    assert out["BEST_SCORE"] == "0.88"
    assert (ws / "train.py").read_text() == "lr = 3\n"


def test_target_met_flag(ws):
    patch = _patch_for(ws, "lr = 4\n")
    _summary(ws)
    out = _run(ws, candidate="0.96", best="0.90", patch=str(patch))
    assert out["TARGET_MET"] == "1"


def test_unappliable_patch_is_a_failed_round(ws):
    bad = ws / "bad.patch"
    bad.write_text("not a patch\n")
    _summary(ws)
    out = _run(ws, candidate="0.9", best="0.85", patch=str(bad))
    assert out["BEST_SCORE"] == "0.85"
    assert out["FAILURES"] == "1"


def test_ledger_and_research_log_append(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    _run(ws, candidate="0.9", best="0.85", patch=str(patch))
    rec = json.loads((ws / "rounds.jsonl").read_text().splitlines()[0])
    assert rec["kept"] is True
    assert len(rec["proposals"]) == 3
    log = (ws / "research_log.md").read_text()
    assert "Round 1" in log and "KEPT" in log and "FAILED" in log
