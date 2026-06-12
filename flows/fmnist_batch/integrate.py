#!/usr/bin/env python3
"""Integrate one batch round: apply the winning patch if it improves on the
best, keep the ledger, emit the captures the coordinator loop runs on.
Deterministic — no LLM. cwd = workspace.

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, help="round best (from run_batch)")
    ap.add_argument("--best", required=True, help="best so far")
    ap.add_argument("--patch", default="", help="winning experiment.patch path")
    ap.add_argument("--summary", required=True, help="round summary.json path")
    ap.add_argument("--target", required=True)
    ap.add_argument("--failures", type=int, required=True,
                    help="consecutive failed rounds so far")
    ap.add_argument("--round", type=int, required=True)
    args = ap.parse_args()

    candidate, best, target = fnum(args.candidate), fnum(args.best), fnum(args.target)
    improved = not math.isnan(candidate) and (math.isnan(best) or candidate > best)

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
                f"round {args.round}: {kept_title or 'experiment'} "
                f"val={candidate:.4f}")
    elif improved:
        improved = False                       # a score but no patch — can't keep

    new_best = candidate if improved else best
    failures = 0 if improved else args.failures + 1
    target_met = int(not math.isnan(new_best) and not math.isnan(target)
                     and new_best >= target)

    _log_round(args.round, summary, candidate, new_best, improved, kept_title)

    print(f"BEST_SCORE={new_best}")
    print(f"FAILURES={failures}")
    print(f"TARGET_MET={target_met}")
    print(f"ROUND_NO={args.round + 1}")
    return 0


def _title(proposal_path: str) -> str:
    try:
        first = Path(proposal_path).read_text().strip().splitlines()[0]
        return first.lstrip("# ").strip()[:70]
    except Exception:
        return ""


def _log_round(round_no: int, summary: dict, candidate: float,
               new_best: float, improved: bool, kept_title: str) -> None:
    record = {
        "round": round_no,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "round_best": None if math.isnan(candidate) else candidate,
        "kept": improved,
        "best_after": None if math.isnan(new_best) else new_best,
        "proposals": [
            {"index": p.get("index"), "title": _title(p.get("proposal", "")),
             "status": p.get("status"), "score": p.get("score")}
            for p in summary.get("proposals", [])
        ],
    }
    with open("rounds.jsonl", "a") as fh:
        fh.write(json.dumps(record) + "\n")

    lines = [f"\n## Round {round_no}\n"]
    for p in record["proposals"]:
        score = "FAILED" if p["score"] is None else f"{p['score']:.4f}"
        lines.append(f"- p{p['index']}: {p['title'] or '(untitled)'} — "
                     f"{score} ({p['status']})")
    verdict = (f"KEPT p{summary.get('best_index')} ({kept_title}) — "
               f"new best {new_best:.4f}" if improved else
               "no improvement — baseline unchanged" if math.isnan(candidate)
               or record["round_best"] is None else
               f"round best {candidate:.4f} did not beat {new_best:.4f}")
    lines.append(f"\n**Verdict:** {verdict}\n")
    with open("research_log.md", "a") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
