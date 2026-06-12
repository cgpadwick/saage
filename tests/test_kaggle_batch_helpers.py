"""Deterministic helpers of the batched kaggle hill-climb: integrate_batch
(direction-aware keep/ledger) and worker_setup (excludes + data link)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FLOW = Path(__file__).resolve().parent.parent / "flows" / "kaggle_solver"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def ws(tmp_path):
    repo = tmp_path / "ws"          # sibling of any cache dir a test makes
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "train.py").write_text("lr = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _patch_for(ws, content: str) -> Path:
    (ws / "train.py").write_text(content)
    diff = subprocess.run(["git", "diff", "--binary"], cwd=ws,
                          capture_output=True, text=True).stdout
    subprocess.run(["git", "checkout", "--", "train.py"], cwd=ws, check=True,
                   capture_output=True)
    p = ws / "winner.patch"
    p.write_text(diff)
    return p


def _summary(ws, best_index=0, scores=(0.4, 0.5, None)) -> Path:
    props = []
    for i, s in enumerate(scores):
        pf = ws / f"prop{i}.md"
        pf.write_text(f"# idea {i} [cat]\n")
        props.append({"index": i, "proposal": str(pf), "score": s,
                      "status": "done" if s is not None else "failed"})
    out = ws / "summary.json"
    out.write_text(json.dumps({"proposals": props, "best_index": best_index}))
    return out


def _integrate(ws, **kv):
    defaults = {"candidate": "0.4", "best": "0.5", "lower-is-better": "true",
                "patch": "", "summary": str(ws / "summary.json"),
                "target": "", "failures": "0", "round": "0"}
    defaults.update({k.replace("_", "-"): v for k, v in kv.items()})
    argv = [sys.executable, str(FLOW / "integrate_batch.py")]
    for k, v in defaults.items():
        argv += [f"--{k}", str(v)]
    proc = subprocess.run(argv, cwd=ws, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines()
                if "=" in line and line.split("=")[0].isupper())


# ---- integrate_batch: direction-aware keep ---------------------------------

def test_lower_is_better_keeps_lower(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    out = _integrate(ws, candidate="0.4", best="0.5", patch=str(patch))
    assert out["BEST_SCORE"] == "0.4"
    assert out["FAILURES"] == "0"
    assert (ws / "train.py").read_text() == "lr = 2\n"


def test_lower_is_better_rejects_higher(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    out = _integrate(ws, candidate="0.6", best="0.5", patch=str(patch))
    assert out["BEST_SCORE"] == "0.5"
    assert out["FAILURES"] == "1"
    assert (ws / "train.py").read_text() == "lr = 1\n"


def test_higher_is_better_direction(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    out = _integrate(ws, candidate="0.9", best="0.85",
                     lower_is_better="false", patch=str(patch))
    assert out["BEST_SCORE"] == "0.9"


def test_template_renders_python_bool_capitalization(ws):
    # flow templates render YAML bools as "True"/"False" — must still parse
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    out = _integrate(ws, candidate="0.4", best="0.5",
                     lower_is_better="True", patch=str(patch))
    assert out["BEST_SCORE"] == "0.4"


def test_target_met_direction_aware(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    out = _integrate(ws, candidate="0.30", best="0.5", patch=str(patch),
                     target="0.35")
    assert out["TARGET_MET"] == "1"
    out2 = _integrate(ws, candidate="0.95", best="0.9",
                      lower_is_better="false", patch=str(patch), target="0.99")
    assert out2["TARGET_MET"] == "0"


def test_nan_round_counts_failure_and_ledgers(ws):
    _summary(ws, scores=(None, None, None))
    out = _integrate(ws, candidate="nan", best="0.5", failures="1")
    assert out["BEST_SCORE"] == "0.5"
    assert out["FAILURES"] == "2"
    recs = [json.loads(l) for l in (ws / "experiments.jsonl").read_text().splitlines()]
    assert len(recs) == 3 and all(r["status"] == "failed" for r in recs)
    assert "every experiment failed" in (ws / "research_log.md").read_text()


def test_ledger_one_record_per_slot_with_winner_marked(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws, best_index=0)
    _integrate(ws, candidate="0.4", best="0.5", patch=str(patch))
    recs = [json.loads(l) for l in (ws / "experiments.jsonl").read_text().splitlines()]
    assert [r["slot"] for r in recs] == [0, 1, 2]
    assert [r["kept"] for r in recs] == [True, False, False]


# ---- worker_setup: excludes + data link ------------------------------------

def _worker_setup(ws, comp, cache):
    env = {**os.environ, "SAAGE_CACHE": str(cache)}
    return subprocess.run(
        [sys.executable, str(FLOW / "worker_setup.py"), "--comp", comp],
        cwd=ws, capture_output=True, text=True, env=env)


def test_worker_setup_links_data_and_writes_excludes(ws, tmp_path):
    cache = tmp_path / "cache"
    src = cache / "datasets" / "mlebench" / "spooky" / "public"
    src.mkdir(parents=True)
    (src / "train.csv").write_text("a,b\n")
    proc = _worker_setup(ws, "spooky", cache)
    assert proc.returncode == 0, proc.stderr
    assert "DATA_READY=1" in proc.stdout
    assert (ws / "data" / "train.csv").exists()
    excl = (ws / ".git" / "info" / "exclude").read_text()
    for pat in ("data", "experiment.patch", "proposal.md", "checkpoints/"):
        assert pat in excl
    # the point of the excludes: a patch made on the worker stays clean
    (ws / "eval_results.json").write_text("{}")
    (ws / "proposal.md").write_text("p")
    (ws / "train.py").write_text("lr = 3\n")
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True, capture_output=True)
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            cwd=ws, capture_output=True, text=True).stdout.split()
    assert staged == ["train.py"]


def test_worker_setup_fails_loud_without_data(ws, tmp_path):
    proc = _worker_setup(ws, "spooky", tmp_path / "empty_cache")
    assert proc.returncode == 1
    assert "DATA_READY=0" in proc.stdout


def test_worker_setup_idempotent(ws, tmp_path):
    cache = tmp_path / "cache"
    src = cache / "datasets" / "mlebench" / "spooky" / "public"
    src.mkdir(parents=True)
    (src / "train.csv").write_text("a,b\n")
    assert _worker_setup(ws, "spooky", cache).returncode == 0
    assert _worker_setup(ws, "spooky", cache).returncode == 0
    excl = (ws / ".git" / "info" / "exclude").read_text()
    assert excl.count("experiment.patch") == 1          # no duplicate appends


def test_noise_level_improvement_is_rejected(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    # 4e-8 better on a 0.5 logloss — rerun jitter, must NOT be kept
    out = _integrate(ws, candidate="0.5039050271", best="0.5039050688",
                     patch=str(patch))
    assert out["BEST_SCORE"] == "0.5039050688"
    assert out["FAILURES"] == "1"
    assert (ws / "train.py").read_text() == "lr = 1\n"


def test_real_improvement_clears_the_noise_floor(ws):
    patch = _patch_for(ws, "lr = 2\n")
    _summary(ws)
    out = _integrate(ws, candidate="0.4990", best="0.5039", patch=str(patch))
    assert out["BEST_SCORE"] == "0.499"


# ---- check_ideas: the researcher's menu gate --------------------------------

def _check_ideas(ws):
    proc = subprocess.run([sys.executable, str(FLOW / "check_ideas.py")],
                          cwd=ws, capture_output=True, text=True)
    assert proc.returncode == 0
    return proc.stdout


GOOD_MENU = "\n".join(
    ["# Autoresearch ideas: spooky", "",
     "**Hard constraints (do not violate):** budget fixed.", "",
     "## Ranked ideas"]
    + [f"### {i}. Idea {i} [{cat}]\nDo the thing.\n"
       for i, cat in enumerate(
           ["Feature Representation", "Model Family", "Optimization",
            "Regularization", "Data Handling", "Ensembling"], start=1)]
    + ["## Anti-ideas", "- flips — known to hurt"])


def test_check_ideas_passes_good_menu(ws):
    (ws / "autoresearch_ideas.md").write_text(GOOD_MENU)
    assert "ACTION: pass" in _check_ideas(ws)


def test_check_ideas_fails_missing_file(ws):
    out = _check_ideas(ws)
    assert "does not exist" in out and "ACTION: fail" in out


def test_check_ideas_fails_thin_or_uncategorized_menus(ws):
    (ws / "autoresearch_ideas.md").write_text(
        "# x\nconstraints\n## Ranked ideas\n### 1. A [cat]\n## Anti-ideas\n- y")
    out = _check_ideas(ws)
    assert "only 1 ranked" in out and "ACTION: fail" in out
    (ws / "autoresearch_ideas.md").write_text(GOOD_MENU.replace(
        "## Anti-ideas\n- flips — known to hurt", ""))
    assert "Anti-ideas" in _check_ideas(ws)
