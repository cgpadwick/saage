#!/usr/bin/env python3
"""Integrate one batched hill-climb round (deterministic — no LLM).
Direction-aware sibling of keep_or_revert.py for the batch variant: apply
the round's winning experiment.patch if it improves the best, append the
round to experiments.jsonl + research_log.md, emit loop captures.
cwd = the competition workspace.

Prints: BEST_SCORE=<float|nan> FAILURES=<int> TARGET_MET=<0|1> ROUND_NO=<int>
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def fnum(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return float("nan")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.email=saage@local",
                           "-c", "user.name=saage", *args],
                          capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--candidate", required=True, help="round best (run_batch)")
    ap.add_argument("--best", required=True)
    ap.add_argument("--lower-is-better", default="false", type=str.lower)
    ap.add_argument("--patch", default="")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--target", default="")
    ap.add_argument("--failures", type=int, required=True)
    ap.add_argument("--round", type=int, required=True)
    args = ap.parse_args()

    lower = args.lower_is_better == "true"
    candidate, best = fnum(args.candidate), fnum(args.best)
    target = fnum(args.target) if args.target else float("nan")
    # noise floor: an "improvement" smaller than 0.01% of the incumbent is
    # rerun jitter (seen live: a no-op experiment "winning" by 4e-8 logloss),
    # not a result — keeping it would pollute the ledger and the git history
    margin = abs(best) * 1e-4 if not math.isnan(best) else 0.0
    improved = not math.isnan(candidate) and (
        math.isnan(best)
        or (candidate < best - margin if lower else candidate > best + margin))

    try:
        summary = json.loads(Path(args.summary).read_text())
    except Exception:
        summary = {"proposals": []}

    kept_title = ""
    if improved and args.patch and Path(args.patch).is_file():
        apply = git("apply", "--binary", "--whitespace=nowarn", args.patch)
        if apply.returncode != 0:
            print(f"PATCH FAILED TO APPLY: {apply.stderr[-500:]}", file=sys.stderr)
            improved = False
        else:
            winner = next((p for p in summary["proposals"]
                           if p["index"] == summary.get("best_index")), {})
            kept_title = _title(winner.get("proposal", ""))
            git("add", "-A")
            git("commit", "-q", "-m",
                f"saage: round {args.round} keep {kept_title or 'experiment'} "
                f"score {candidate}")
    elif improved:
        improved = False                  # score without a patch — can't keep

    new_best = candidate if improved else best
    failures = 0 if improved else args.failures + 1
    met = (not math.isnan(new_best) and not math.isnan(target)
           and (new_best <= target if lower else new_best >= target))

    _ledger(args.round, summary, candidate, new_best, improved, kept_title)

    print(f"BEST_SCORE={new_best}")
    print(f"FAILURES={failures}")
    print(f"TARGET_MET={int(met)}")
    print(f"ROUND_NO={args.round + 1}")
    return 0


def _title(proposal_path: str) -> str:
    try:
        first = Path(proposal_path).read_text().strip().splitlines()[0]
        return first.lstrip("# ").strip()[:70]
    except Exception:
        return ""


def _ledger(round_no: int, summary: dict, candidate: float, new_best: float,
            improved: bool, kept_title: str) -> None:
    # one experiments.jsonl record per parallel proposal — same ledger the
    # sequential flow keeps, so downstream tooling reads either variant
    with open("experiments.jsonl", "a") as fh:
        for p in summary.get("proposals", []):
            fh.write(json.dumps({
                "round": round_no, "slot": p.get("index"),
                "candidate": p.get("score"),
                "best": None if math.isnan(new_best) else new_best,
                "kept": improved and p.get("index") == summary.get("best_index"),
                "proposal": _title(p.get("proposal", "")),
                "status": p.get("status"),
            }) + "\n")

    lines = [f"\n## Round {round_no} "
             f"({datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')})\n"]
    for p in summary.get("proposals", []):
        score = "FAILED" if p.get("score") is None else f"{p['score']:.5g}"
        lines.append(f"- p{p.get('index')}: {_title(p.get('proposal', '')) or '(untitled)'}"
                     f" — {score} ({p.get('status')})")
    if improved:
        verdict = (f"KEPT p{summary.get('best_index')} ({kept_title}) — "
                   f"new best {new_best:.5g}")
    elif math.isnan(candidate):
        verdict = "every experiment failed — best unchanged"
    else:
        verdict = f"round best {candidate:.5g} did not beat {new_best:.5g}"
    lines.append(f"\n**Verdict:** {verdict}\n")
    with open("research_log.md", "a") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
