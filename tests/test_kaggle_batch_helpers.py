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


# ---- check_ideas: structured menu validation + rendering ---------------------

def _check_ideas(ws):
    proc = subprocess.run([sys.executable, str(FLOW / "check_ideas.py")],
                          cwd=ws, capture_output=True, text=True)
    assert proc.returncode == 0
    return proc.stdout


def _menu(n=6, cats=("feature representation", "model family",
                     "optimization & schedule", "regularization",
                     "data handling", "ensembling")):
    return {
        "constraints": ["budget fixed", "contract frozen"],
        "ideas": [{"rank": i + 1, "title": f"Idea {i + 1}",
                   "category": cats[i % len(cats)],
                   "change": f"change number {i + 1}", "why": "EDA says so",
                   "cost": "fits budget"} for i in range(n)],
        "anti_ideas": [{"technique": "flips", "reason": "hurts this metric"}],
    }


def _write_menu(ws, doc):
    (ws / "autoresearch_ideas.json").write_text(json.dumps(doc))


def test_check_ideas_passes_and_renders(ws):
    _write_menu(ws, _menu())
    out = _check_ideas(ws)
    assert "ACTION: pass" in out
    md = (ws / "autoresearch_ideas.md").read_text()
    assert "### 1. Idea 1 [feature representation]" in md
    assert "## Anti-ideas" in md and "flips" in md


def test_check_ideas_fails_missing_or_invalid_json(ws):
    assert "does not exist" in _check_ideas(ws)
    (ws / "autoresearch_ideas.json").write_text("{not json")
    assert "not valid JSON" in _check_ideas(ws)


def test_check_ideas_fails_structural_problems(ws):
    doc = _menu()
    doc["ideas"][2]["change"] = "  "                     # empty field
    _write_menu(ws, doc)
    assert "missing/empty field 'change'" in _check_ideas(ws)

    doc = _menu()
    doc["ideas"][3]["rank"] = 99                         # broken ranks
    _write_menu(ws, doc)
    assert "unique and contiguous" in _check_ideas(ws)

    doc = _menu()
    for i in doc["ideas"]:
        i["category"] = "model family"                   # fake diversity
    _write_menu(ws, doc)
    assert "distinct categories" in _check_ideas(ws)

    doc = _menu(4)                                       # too thin
    _write_menu(ws, doc)
    assert "at least 6" in _check_ideas(ws)


def test_check_ideas_fails_exact_duplicates(ws):
    doc = _menu()
    doc["ideas"][4]["title"] = doc["ideas"][1]["title"]
    _write_menu(ws, doc)
    out = _check_ideas(ws)
    assert "identical title" in out and "ACTION: fail" in out


def test_check_ideas_requires_real_anti_ideas(ws):
    doc = _menu()
    doc["anti_ideas"] = [{"technique": "x", "reason": ""}]
    _write_menu(ws, doc)
    assert "anti_ideas[0]" in _check_ideas(ws)
    doc["anti_ideas"] = []
    _write_menu(ws, doc)
    assert "non-empty list" in _check_ideas(ws)


def test_keep_or_revert_tolerates_batch_ledger_rows(tmp_path, monkeypatch):
    """Batched rounds write round/slot rows (no 'step') into the same ledger;
    the sequential keep_or_revert must not KeyError on them — seen live: the
    post-final-train ensemble gate crashed on a sweep's ledger."""
    import subprocess, sys, json
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    # a batch-round row (no "step") followed by nothing else
    Path("experiments.jsonl").write_text(json.dumps(
        {"round": 3, "slot": 1, "candidate": 0.4, "kept": True}) + "\n")
    script = Path(__file__).resolve().parent.parent / "flows" / "kaggle_solver" / "keep_or_revert.py"
    r = subprocess.run([sys.executable, str(script), "--candidate", "0.55",
                        "--best", "nan", "--failures", "0",
                        "--lower-is-better", "true", "--target", ""],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "RESULT=revert" in r.stdout


def test_integrate_ledger_rows_carry_step(tmp_path, monkeypatch):
    import importlib.util, sys as _sys, json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "kaggle_integrate_batch",
        Path(__file__).resolve().parent.parent / "flows" / "kaggle_solver" / "integrate_batch.py")
    m = importlib.util.module_from_spec(spec)
    _sys.modules["kaggle_integrate_batch"] = m
    spec.loader.exec_module(m)
    monkeypatch.chdir(tmp_path)
    Path("experiments.jsonl").write_text(json.dumps({"step": 4, "kept": True}) + "\n")
    summary = {"proposals": [{"index": 0, "score": 0.5, "status": "done",
                              "proposal": "a"},
                             {"index": 1, "score": 0.4, "status": "done",
                              "proposal": "b"}],
               "best_index": 1}
    m._ledger(2, summary, 0.4, 0.4, True, "t")
    rows = [json.loads(l) for l in Path("experiments.jsonl").read_text().splitlines()]
    assert [r.get("step") for r in rows] == [4, 5, 6]


def test_integrate_smoke_failure_reverts_patch(tmp_path, monkeypatch):
    """The env-skew guard: a winning patch that applies but cannot EXECUTE on
    the coordinator is reverted and the round counts as failed."""
    import subprocess, sys, json
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    Path("code.py").write_text("ORIGINAL\n")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "base"], check=True)
    # a patch that changes code.py
    Path("code.py").write_text("PATCHED\n")
    diff = subprocess.run(["git", "diff", "--binary"], capture_output=True,
                          text=True, check=True).stdout
    subprocess.run(["git", "checkout", "--", "."], check=True)
    Path("win.patch").write_text(diff)
    # production keeps round artifacts git-excluded (setup_competition.py), so
    # the smoke-revert's `git clean -fd` can't eat them — mirror that here
    Path(".git/info/exclude").write_text("win.patch\nsummary.json\n")
    Path("summary.json").write_text(json.dumps(
        {"proposals": [{"index": 0, "score": 0.3, "status": "done",
                        "proposal": ""}], "best_index": 0}))
    script = (Path(__file__).resolve().parent.parent / "flows" /
              "kaggle_solver" / "integrate_batch.py")
    r = subprocess.run([sys.executable, str(script),
                        "--candidate", "0.3", "--best", "0.5",
                        "--lower-is-better", "true",
                        "--patch", "win.patch", "--summary", "summary.json",
                        "--failures", "0", "--round", "1",
                        "--smoke-cmd", "exit 3"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "SMOKE FAILED" in r.stderr
    assert "BEST_SCORE=0.5" in r.stdout          # round did NOT improve
    assert "FAILURES=1" in r.stdout
    assert Path("code.py").read_text() == "ORIGINAL\n"   # patch reverted

    # and with a passing smoke, the same patch is kept
    r2 = subprocess.run([sys.executable, str(script),
                         "--candidate", "0.3", "--best", "0.5",
                         "--lower-is-better", "true",
                         "--patch", "win.patch", "--summary", "summary.json",
                         "--failures", "0", "--round", "2",
                         "--smoke-cmd", "true"],
                        capture_output=True, text=True)
    assert "BEST_SCORE=0.3" in r2.stdout
    assert Path("code.py").read_text() == "PATCHED\n"
