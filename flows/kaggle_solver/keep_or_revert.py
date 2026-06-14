#!/usr/bin/env python3
"""Keep or revert a hill-climb experiment (deterministic — no LLM).

Direction-aware port of greenfield's helper + mle-beast's HillClimbEvalNode,
extended for the kaggle flow:

  - `--candidate nan` (the per-iteration reset sentinel) always reverts —
    NaN compares False both directions, so a failed train/eval can never
    "improve" the best regardless of metric direction.
  - prints `TARGET_MET=0|1` (direction-aware vs `--target`, when given) so
    the loop's exit_when stays a trivial predicate.
  - `--baseline true` records/commits the first score without comparison.

Prints `RESULT=... BEST_SCORE=... FAILURES=... TARGET_MET=...` for `set:`
captures. Runs with cwd = the workspace, so git operates on the workspace repo.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess


def git(*args: str) -> None:
    subprocess.run(["git", "-c", "user.email=saage@local", "-c", "user.name=saage", *args],
                   check=False, capture_output=True)


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--candidate", type=float, required=True)
    ap.add_argument("--best", type=float, required=True)
    ap.add_argument("--failures", type=int, default=0)
    ap.add_argument("--lower-is-better", default="false")
    ap.add_argument("--target", default="",
                    help="optional target score; sets TARGET_MET (direction-aware)")
    ap.add_argument("--baseline", default="false",
                    help="true = record the first score, no comparison")
    args = ap.parse_args()

    cand, best, fails = args.candidate, args.best, args.failures
    lower = str(args.lower_is_better).lower() == "true"
    baseline = str(args.baseline).lower() == "true"

    if baseline and not math.isnan(cand):
        git("add", "-A")
        git("commit", "-m", f"saage: baseline score {cand}")
        best, fails, status, kept = cand, 0, "keep", True
    else:
        # strict inequality: ties revert (no equal-score churn); NaN candidates
        # (failed train/eval) compare False and revert in both directions
        improved = (cand < best) if lower else (cand > best)
        if improved:
            git("add", "-A")
            git("commit", "-m", f"saage: keep score {cand}")
            best, fails, status, kept = cand, 0, "keep", True
        else:
            # preserve the research log across the revert (excluded files —
            # data, ledger, proposals — are untouched by checkout/clean)
            saved = (open("research_log.md").read()
                     if os.path.exists("research_log.md") else "")
            git("checkout", "--", ".")
            git("clean", "-fd")
            if saved:
                open("research_log.md", "w").write(saved)
            fails, status, kept = fails + 1, "revert", False

    target_met = 0
    if args.target not in ("", "none", "None") and not math.isnan(best):
        t = float(args.target)
        target_met = int(best <= t if lower else best >= t)

    with open("research_log.md", "a") as f:
        f.write(f"- candidate={cand} best={best} -> {status}\n")
    _record_experiment(cand, best, kept)

    print(f"RESULT={status} BEST_SCORE={best} FAILURES={fails} TARGET_MET={target_met}")


def _record_experiment(candidate: float, best: float, kept: bool) -> None:
    step = 1
    if os.path.exists("experiments.jsonl"):
        step = 1 + sum(1 for _ in open("experiments.jsonl"))
    proposal = ""
    if os.path.exists("proposals/latest.md"):
        proposal = open("proposals/latest.md").read().strip()
    record = {"step": step,
              "candidate": None if math.isnan(candidate) else candidate,
              "best": None if math.isnan(best) else best,
              "kept": kept, "proposal": proposal}
    with open("experiments.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
