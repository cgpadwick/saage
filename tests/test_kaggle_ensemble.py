"""Generic prediction-pool ensembling: pool_archive.py + blend_ensemble.py.

The design under test: every scored experiment archives submission-shaped
predictions; a deterministic blender does Caruana greedy selection against
the solution's own score_preds.py (black-box metric), gated on a
confirmation slice the search never saw. Every exit path is exit 0 with a
BLEND=/POOL= verdict — ensembling upgrades a run, never fails one.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

FLOW = Path(__file__).resolve().parent.parent / "flows" / "kaggle_solver"

# black-box metric fixture: MSE on the 'p' column (lower is better)
SCORE_PREDS = """\
import csv, sys
preds = {r["id"]: float(r["p"]) for r in csv.DictReader(open(sys.argv[1]))}
labels = {r["id"]: float(r["label"]) for r in csv.DictReader(open(sys.argv[2]))}
ids = sorted(set(preds) & set(labels))
mse = sum((preds[i] - labels[i]) ** 2 for i in ids) / len(ids)
print(f"SCORE={mse}")
"""


def _write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _member(ws, seq, tag, score, val_rows, test_rows,
            header=("id", "p")):
    d = ws / "ensemble_pool" / f"{seq:03d}_{tag}"
    _write_csv(d / "val_preds.csv", header, val_rows)
    _write_csv(d / "test_preds.csv", header, test_rows)
    _write_csv(d / "val_labels.csv", ("id", "label"),
               [(f"v{i}", TRUTH[i]) for i in range(N)])
    (d / "meta.json").write_text(json.dumps(
        {"score": score, "tag": tag, "seq": seq}))


N = 100
TRUTH = [(i % 7) / 7.0 for i in range(N)]          # deterministic "labels"


def _run(script, ws, *args):
    return subprocess.run([sys.executable, str(FLOW / script), *args],
                          cwd=ws, capture_output=True, text=True)


def _setup_ws(tmp_path):
    ws = tmp_path
    (ws / "score_preds.py").write_text(SCORE_PREDS)
    return ws


# ------------------------------------------------------------------ blend --

def test_anticorrelated_members_blend_and_apply(tmp_path):
    ws = _setup_ws(tmp_path)
    e = 0.2
    val_a = [(f"v{i}", TRUTH[i] + e * (-1) ** i) for i in range(N)]
    val_b = [(f"v{i}", TRUTH[i] - e * (-1) ** i) for i in range(N)]
    test_a = [(f"t{i}", 0.2) for i in range(20)]
    test_b = [(f"t{i}", 0.6) for i in range(20)]
    _member(ws, 0, "a", 0.04, val_a, test_a)
    _member(ws, 1, "b", 0.04, val_b, test_b)
    r = _run("blend_ensemble.py", ws, "--lower-is-better", "true")
    assert "BLEND=applied" in r.stdout, r.stdout + r.stderr
    # blended test preds are the (equal-weight) average of the members
    sub = {row["id"]: float(row["p"])
           for row in csv.DictReader(open(ws / "submission.csv"))}
    assert abs(sub["t0"] - 0.4) < 1e-9


def test_blend_declined_keeps_solo_submission(tmp_path):
    ws = _setup_ws(tmp_path)
    perfect = [(f"v{i}", TRUTH[i]) for i in range(N)]
    garbage = [(f"v{i}", 1.0 - TRUTH[i]) for i in range(N)]
    test = [(f"t{i}", 0.5) for i in range(20)]
    _member(ws, 0, "good", 0.0, perfect, test)
    _member(ws, 1, "bad", 0.9, garbage, test)
    (ws / "submission.csv").write_text("id,p\nt0,0.123\n")
    r = _run("blend_ensemble.py", ws, "--lower-is-better", "true")
    # greedy keeps only the perfect member -> no strict improvement on
    # confirmation -> declined, submission untouched
    assert "BLEND=declined" in r.stdout, r.stdout + r.stderr
    assert (ws / "submission.csv").read_text() == "id,p\nt0,0.123\n"


def test_pool_too_small_skips(tmp_path):
    ws = _setup_ws(tmp_path)
    _member(ws, 0, "only", 0.1,
            [(f"v{i}", TRUTH[i]) for i in range(N)],
            [(f"t{i}", 0.5) for i in range(20)])
    r = _run("blend_ensemble.py", ws, "--lower-is-better", "true")
    assert "BLEND=skipped" in r.stdout and "pool-too-small" in r.stdout
    assert r.returncode == 0


def test_missing_score_script_skips(tmp_path):
    r = _run("blend_ensemble.py", tmp_path, "--lower-is-better", "true")
    assert "BLEND=skipped" in r.stdout and r.returncode == 0


def test_duplicate_members_collapse(tmp_path):
    ws = _setup_ws(tmp_path)
    same = [(f"v{i}", TRUTH[i] + 0.1) for i in range(N)]
    test = [(f"t{i}", 0.5) for i in range(20)]
    _member(ws, 0, "a", 0.01, same, test)
    _member(ws, 1, "b", 0.01, same, test)         # exact clone
    r = _run("blend_ensemble.py", ws, "--lower-is-better", "true")
    assert "BLEND=skipped" in r.stdout and "all-duplicates" in r.stdout


def test_mismatched_header_member_excluded(tmp_path):
    ws = _setup_ws(tmp_path)
    e = 0.2
    _member(ws, 0, "a", 0.04,
            [(f"v{i}", TRUTH[i] + e * (-1) ** i) for i in range(N)],
            [(f"t{i}", 0.2) for i in range(20)])
    _member(ws, 1, "b", 0.04,
            [(f"v{i}", TRUTH[i] - e * (-1) ** i) for i in range(N)],
            [(f"t{i}", 0.6) for i in range(20)])
    _member(ws, 2, "alien", 0.01,
            [(f"v{i}", TRUTH[i]) for i in range(N)],
            [(f"t{i}", 0.5) for i in range(20)],
            header=("id", "other_col"))
    r = _run("blend_ensemble.py", ws, "--lower-is-better", "true")
    assert "BLEND=applied" in r.stdout, r.stdout + r.stderr
    assert "alien" not in r.stdout                # excluded from the picks


# ------------------------------------------------------------------- pool --

def test_pool_archive_copies_and_tags(tmp_path):
    ws = tmp_path
    _write_csv(ws / "predictions" / "val_preds.csv", ("id", "p"), [("v0", 0.5)])
    _write_csv(ws / "predictions" / "test_preds.csv", ("id", "p"), [("t0", 0.5)])
    _write_csv(ws / "predictions" / "val_labels.csv", ("id", "label"), [("v0", 1)])
    (ws / "eval_results.json").write_text('{"metric_name": "m", "value": 0.42}')
    r = _run("pool_archive.py", ws, "--tag", "hillclimb")
    assert "POOL=archived" in r.stdout and r.returncode == 0
    meta = json.loads((ws / "ensemble_pool" / "000_hillclimb" / "meta.json").read_text())
    assert meta["score"] == 0.42
    # second archive gets the next sequence number
    r = _run("pool_archive.py", ws, "--tag", "hillclimb")
    assert "seq=1" in r.stdout


def test_pool_archive_tolerates_missing_contract(tmp_path):
    (tmp_path / "eval_results.json").write_text('{"value": 0.5}')
    r = _run("pool_archive.py", tmp_path, "--tag", "x")
    assert "POOL=skipped" in r.stdout and r.returncode == 0


# ------------------------------------------------------------ no-op probe --

TRAIN_STUB = """\
import csv, json, pathlib, sys
pathlib.Path("predictions").mkdir(exist_ok=True)
with open("predictions/val_preds.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["id", "p"])
    for i in range(5): w.writerow([f"v{i}", VALUE])
json.dump({"metric_name": "m", "value": 0.5}, open("eval_results.json", "w"))
"""


def _probe_ws(tmp_path, champion_value, train_value):
    ws = tmp_path
    d = ws / "ensemble_pool" / "000_baseline"
    _write_csv(d / "val_preds.csv", ("id", "p"),
               [(f"v{i}", champion_value) for i in range(5)])
    (ws / "train.py").write_text(TRAIN_STUB.replace("VALUE", repr(train_value)))
    (ws / "data").mkdir(exist_ok=True)
    return ws


def test_probe_fails_on_identical_predictions(tmp_path):
    ws = _probe_ws(tmp_path, "0.7", "0.7")
    r = _run("no_op_probe.py", ws)
    assert r.returncode == 1 and "BYTE-IDENTICAL" in r.stdout


def test_probe_passes_on_changed_predictions(tmp_path):
    ws = _probe_ws(tmp_path, "0.7", "0.9")
    r = _run("no_op_probe.py", ws)
    assert r.returncode == 0 and "NOOP_PROBE=pass" in r.stdout


def test_probe_fails_open_without_pool(tmp_path):
    (tmp_path / "train.py").write_text("raise SystemExit(0)")
    r = _run("no_op_probe.py", tmp_path)
    assert r.returncode == 0 and "no-reference" in r.stdout


def test_probe_fails_open_on_crashing_train(tmp_path):
    ws = _probe_ws(tmp_path, "0.7", "0.7")
    (ws / "train.py").write_text("raise RuntimeError('boom')")
    r = _run("no_op_probe.py", ws)
    assert r.returncode == 0 and "fail-open" in r.stdout
