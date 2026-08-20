#!/usr/bin/env python3
"""Generic prediction-space ensembling over the run's own experiment pool.

Domain-blind by construction: members are submission-shaped prediction files
(any competition, any model family), and the metric is a black box — the
solution's own `score_preds.py` (train.py contract) scores any candidate
blend. Selection is Caruana-style greedy forward selection WITH replacement:
start empty, repeatedly add whichever member most improves the blend on the
SELECTION slice of validation rows, stop when nothing improves. Pick counts
become the blend weights.

Selection-overfitting guard: validation rows are split (deterministically)
into a selection slice and a CONFIRMATION slice the greedy search never sees;
the blend ships only if it beats the best single member on confirmation.
Every exit path is exit 0 with a `BLEND=` verdict line — ensembling is an
upgrade, never a way to fail the run. When it wins, submission.csv is
replaced with the blended test predictions (solo copy kept as
submission_solo.csv). cwd = workspace.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_PICKS = 12
MIN_MEMBERS = 2


def read_table(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    """(header, {id: row-values}) — first column is the join key."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    return header, {r[0]: r[1:] for r in body if r}


def score_with(score_script: Path, header: list[str],
               ids: list[str], preds: dict[str, list[float]],
               labels_path: Path) -> float:
    """Score a prediction matrix via the solution's own metric script."""
    with tempfile.NamedTemporaryFile("w", suffix=".csv", newline="",
                                     delete=False, encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i in ids:
            w.writerow([i] + [f"{v:.10g}" for v in preds[i]])
        tmp = f.name
    try:
        out = subprocess.run(
            [sys.executable, str(score_script), tmp, str(labels_path)],
            capture_output=True, text=True, timeout=300)
        for line in reversed(out.stdout.splitlines()):
            if line.startswith("SCORE="):
                return float(line.split("=", 1)[1])
        raise ValueError(f"no SCORE= in score_preds output: "
                         f"{out.stdout[-200:]!r} {out.stderr[-200:]!r}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def blend(matrices: list[dict[str, list[float]]], weights: list[float],
          ids: list[str]) -> dict[str, list[float]]:
    total = sum(weights)
    out: dict[str, list[float]] = {}
    for i in ids:
        ncol = len(matrices[0][i])
        acc = [0.0] * ncol
        for m, w in zip(matrices, weights):
            for c in range(ncol):
                acc[c] += w * m[i][c]
        out[i] = [v / total for v in acc]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lower-is-better", default="true")
    ap.add_argument("--min-gain", type=float, default=0.0,
                    help="confirmation-slice margin the blend must beat the "
                         "best solo member by")
    args = ap.parse_args()
    lower = str(args.lower_is_better).lower() in ("true", "1", "yes")
    better = (lambda a, b: a < b - args.min_gain) if lower else \
             (lambda a, b: a > b + args.min_gain)

    score_script = Path("score_preds.py")
    if not score_script.is_file():
        print("BLEND=skipped reason=no-score_preds.py")
        return 0
    pool = sorted(Path("ensemble_pool").glob("*/")) if Path(
        "ensemble_pool").is_dir() else []
    members = [d for d in pool if (d / "val_preds.csv").is_file()
               and (d / "test_preds.csv").is_file()]
    if len(members) < MIN_MEMBERS:
        print(f"BLEND=skipped reason=pool-too-small n={len(members)}")
        return 0

    # ---- load + align on the id intersection (protocol violators drop out)
    labels_path = members[-1] / "val_labels.csv"
    val_tables, test_tables, metas = [], [], []
    header = None
    for d in members:
        vh, vt = read_table(d / "val_preds.csv")
        th, tt = read_table(d / "test_preds.csv")
        header = header or vh
        if vh != header:
            continue                      # column mismatch: not blendable
        val_tables.append({k: [float(x) for x in v] for k, v in vt.items()})
        test_tables.append({k: [float(x) for x in v] for k, v in tt.items()})
        metas.append(json.loads((d / "meta.json").read_text()))
    ids = sorted(set.intersection(*[set(t) for t in val_tables]))
    test_ids = sorted(set.intersection(*[set(t) for t in test_tables]))
    if len(val_tables) < MIN_MEMBERS or not ids or not test_ids:
        print("BLEND=skipped reason=no-aligned-members")
        return 0

    # drop exact-duplicate prediction matrices (clones add nothing)
    uniq_v, uniq_t, uniq_m, seen = [], [], [], set()
    for v, t, m in zip(val_tables, test_tables, metas):
        sig = tuple(tuple(v[i]) for i in ids[:50])
        if sig in seen:
            continue
        seen.add(sig)
        uniq_v.append(v); uniq_t.append(t); uniq_m.append(m)
    val_tables, test_tables, metas = uniq_v, uniq_t, uniq_m
    if len(val_tables) < MIN_MEMBERS:
        print("BLEND=skipped reason=all-duplicates")
        return 0

    # ---- selection / confirmation split (deterministic, no RNG needed)
    sel_ids = [i for k, i in enumerate(ids) if k % 10 < 7]
    conf_ids = [i for k, i in enumerate(ids) if k % 10 >= 7]

    def sel_score(mat):  # noqa: E306
        return score_with(score_script, header, sel_ids, mat, labels_path)

    def conf_score(mat):
        return score_with(score_script, header, conf_ids, mat, labels_path)

    solo_sel = [sel_score(v) for v in val_tables]
    best_solo_idx = min(range(len(solo_sel)), key=lambda k: solo_sel[k]) \
        if lower else max(range(len(solo_sel)), key=lambda k: solo_sel[k])

    # ---- Caruana greedy forward selection with replacement
    picks = [best_solo_idx]
    cur = sel_score(blend([val_tables[i] for i in picks], [1.0] * len(picks), sel_ids))
    for _ in range(MAX_PICKS - 1):
        best_cand, best_cand_score = None, cur
        for j in range(len(val_tables)):
            trial = picks + [j]
            s = sel_score(blend([val_tables[i] for i in trial],
                                [1.0] * len(trial), sel_ids))
            if better(s, best_cand_score):
                best_cand, best_cand_score = j, s
        if best_cand is None:
            break
        picks.append(best_cand)
        cur = best_cand_score

    # ---- confirmation gate: the search never saw these rows
    weights: dict[int, float] = {}
    for p in picks:
        weights[p] = weights.get(p, 0) + 1.0
    keys = sorted(weights)
    blend_conf = conf_score(blend([val_tables[k] for k in keys],
                                  [weights[k] for k in keys], conf_ids))
    solo_conf = conf_score(val_tables[best_solo_idx])
    if not better(blend_conf, solo_conf):
        print(f"BLEND=declined solo_conf={solo_conf:.6f} "
              f"blend_conf={blend_conf:.6f} members={len(val_tables)}")
        return 0

    # ---- apply: blended test predictions become the submission
    test_blend = blend([test_tables[k] for k in keys],
                       [weights[k] for k in keys], test_ids)
    if Path("submission.csv").is_file():
        shutil.copy2("submission.csv", "submission_solo.csv")
    with open("submission.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i in test_ids:
            w.writerow([i] + [f"{v:.10g}" for v in test_blend[i]])
    picked = {metas[k]["tag"] + f"#{metas[k]['seq']}": weights[k] for k in keys}
    print(f"BLEND=applied solo_conf={solo_conf:.6f} "
          f"blend_conf={blend_conf:.6f} picks={json.dumps(picked)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
