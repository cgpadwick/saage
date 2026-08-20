#!/usr/bin/env python3
"""Archive one experiment's predictions into the ensemble pool.

Runs at the end of every successful train command (`train.py && read_val_score
&& pool_archive`). The hill-climb trains ~30 full solutions and normally keeps
one; their PREDICTIONS are the raw material of a generic ensemble — reverted
candidates are diversity, not garbage. Predictions are domain-blind (always
submission-shaped), so this works for any competition.

Copies predictions/{val_preds,test_preds,val_labels}.csv (the train.py
contract) plus the eval score into ensemble_pool/<seq>_<tag>/ with a
meta.json. Tolerant by design: a solution that predates the contract just
logs a warning and exits 0 — the blend step degrades to a no-op rather than
the run failing. cwd = workspace.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REQUIRED = ("val_preds.csv", "test_preds.csv", "val_labels.csv")


def _git_exclude(pool: Path) -> None:
    """The pool must survive keep_or_revert's `git checkout -- . && git clean
    -fd`: reverted experiments are exactly the decorrelated members the blend
    wants, so their archived predictions must be invisible to git. Seen live:
    every revert deleted the untracked new member."""
    exclude = Path(".git") / "info" / "exclude"
    if not exclude.parent.is_dir():
        return                              # not a git workspace: nothing to do
    line = "ensemble_pool/"
    try:
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if line not in text.splitlines():
            exclude.write_text(text + ("" if text.endswith("\n") or not text
                                       else "\n") + line + "\n",
                               encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="exp", help="which train step archived this")
    args = ap.parse_args()

    preds = Path("predictions")
    missing = [f for f in REQUIRED if not (preds / f).is_file()]
    if missing:
        print(f"POOL=skipped missing={','.join(missing)} "
              f"(train.py predictions contract not met — blend will have "
              f"fewer members)")
        return 0

    try:
        score = json.loads(Path("eval_results.json").read_text())["value"]
    except Exception as e:  # noqa: BLE001
        print(f"POOL=skipped no-eval-score ({e})")
        return 0

    pool = Path("ensemble_pool")
    pool.mkdir(exist_ok=True)
    _git_exclude(pool)
    seq = len(list(pool.iterdir()))
    dest = pool / f"{seq:03d}_{args.tag}"
    dest.mkdir()
    for f in REQUIRED:
        shutil.copy2(preds / f, dest / f)
    (dest / "meta.json").write_text(json.dumps(
        {"score": score, "tag": args.tag, "seq": seq}))
    print(f"POOL=archived seq={seq} tag={args.tag} score={score}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
