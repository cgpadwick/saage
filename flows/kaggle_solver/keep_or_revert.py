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


def git_out(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip()


# harness bookkeeping + generated outputs — not part of an experiment's code
# footprint (eval/submission/log files are produced by train/predict, not edits)
_LEDGER_FILES = {"research_log.md", "experiments.jsonl", "eval_results.json",
                 "submission.csv", "training.log"}


def _changed_files() -> list[str]:
    """The experiment's code footprint vs the last kept commit, minus bookkeeping
    and generated outputs. Captured BEFORE commit/revert so a reverted attempt
    still records what it tried."""
    tracked = git_out("diff", "--name-only", "HEAD").splitlines()
    untracked = git_out("ls-files", "--others", "--exclude-standard").splitlines()
    files = set()
    for path in tracked + untracked:
        path = path.strip()
        if (not path or path in _LEDGER_FILES
                or path.startswith("proposals/") or path.startswith("checkpoints/")):
            continue
        files.add(path)
    return sorted(files)


def _read_file(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read().strip()


def _read_proposal() -> str:
    return _read_file("proposals/latest.md")


def _read_summary() -> str:
    """The summarize agent's one-paragraph digest of the proposal."""
    return _read_file("proposals/summary.md")


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

    # capture the implement footprint, proposal, and summary BEFORE commit/revert
    # — a revert's `git clean` wipes the untracked proposals/ dir
    files_changed = _changed_files()
    proposal = _read_proposal()
    summary = _read_summary()

    if baseline and not math.isnan(cand):
        git("add", "-A")
        git("commit", "-m", f"saage: baseline score {cand}")
        commit_sha = git_out("rev-parse", "HEAD") or None
        best, fails, status, kept = cand, 0, "keep", True
    else:
        # strict inequality: ties revert (no equal-score churn); NaN candidates
        # (failed train/eval) compare False and revert in both directions
        improved = (cand < best) if lower else (cand > best)
        if improved:
            git("add", "-A")
            git("commit", "-m", f"saage: keep score {cand}")
            commit_sha = git_out("rev-parse", "HEAD") or None
            best, fails, status, kept = cand, 0, "keep", True
        else:
            # research_log.md is tracked, so `git checkout -- .` would roll it
            # back to the last kept commit — save its current text and restore it
            # after the revert so this attempt's entry survives. (experiments.jsonl
            # / proposals/ are git-excluded, so checkout/clean leave them alone.)
            saved = _read_file("research_log.md")
            git("checkout", "--", ".")
            git("clean", "-fd")
            if saved:
                with open("research_log.md", "w") as f:
                    f.write(saved + "\n")
            commit_sha = None
            fails, status, kept = fails + 1, "revert", False

    target_met = 0
    if args.target not in ("", "none", "None") and not math.isnan(best):
        t = float(args.target)
        target_met = int(best <= t if lower else best >= t)

    # rich record: terse summary + outcome -> research_log (proposer's working
    # memory); full proposal -> experiments.jsonl (human record / report)
    _record_experiment(cand, best, kept, commit_sha, files_changed, proposal, summary)

    print(f"RESULT={status} BEST_SCORE={best} FAILURES={fails} TARGET_MET={target_met}")


def _fmt(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x:g}"


def _append_research_log(step: int, candidate: float, best: float, kept: bool,
                         commit_sha: str | None, files_changed: list[str],
                         summary: str) -> None:
    """Append the terse entry the next propose/critic agent reads: a one-paragraph
    change summary + the files actually changed + the outcome. The summary (not
    the full proposal) keeps this log small enough to re-read every iteration."""
    result = "KEPT ✅" if kept else "reverted ❌"
    files = ", ".join(files_changed) or "none"
    sha = (commit_sha or "")[:8]
    body = summary or "(no summary written)"
    with open("research_log.md", "a") as f:
        f.write(
            f"\n## Experiment {step} — {result} "
            f"(candidate={_fmt(candidate)}, best={_fmt(best)})\n"
            f"- changed: {files}\n"
            + (f"- commit: {sha}\n" if sha else "")
            + f"\n{body}\n"
        )


def _record_experiment(candidate: float, best: float, kept: bool,
                       commit_sha: str | None, files_changed: list[str],
                       proposal: str, summary: str) -> None:
    rows = []
    if os.path.exists("experiments.jsonl"):
        with open("experiments.jsonl") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    step = len(rows) + 1
    # parent_step = the most recent KEPT step (the experiment this branched off);
    # 0 = the baseline
    parent_step = next((r["step"] for r in reversed(rows) if r.get("kept")), 0)
    _append_research_log(step, candidate, best, kept, commit_sha,
                         files_changed, summary)
    record = {"step": step, "parent_step": parent_step,
              "candidate": None if math.isnan(candidate) else candidate,
              "best": None if math.isnan(best) else best,
              "kept": kept, "commit_sha": commit_sha,
              "files_changed": files_changed, "summary": summary,
              "proposal": proposal}
    with open("experiments.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
