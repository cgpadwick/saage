#!/usr/bin/env python3
"""Keep or revert a hill-climb experiment (deterministic — no LLM).

Compares the candidate score to the current best, honoring the metric direction.
  improved  -> git-commit the change, advance best, reset the failure counter.
  not improved -> git checkout/clean back to the last kept commit (preserving the
                  research log, which `git clean` would otherwise wipe), and
                  increment the failure counter.

Prints `RESULT=... BEST_SCORE=... FAILURES=...` so the flow can capture the new
best score and failure count back into the shared store. Runs in the workspace
(the command's cwd), so git operates on the workspace repo.

Invoked by the flow's keep_or_revert command:
    python3 "{flow_dir}/keep_or_revert.py" --candidate {candidate_score} \
        --best {best_score} --failures {consecutive_failures} \
        --lower-is-better {lower_is_better}
"""
from __future__ import annotations

import argparse
import os
import subprocess


def git(*args: str) -> None:
    # -c identity keeps commits working even without global git config
    subprocess.run(["git", "-c", "user.email=saage@local", "-c", "user.name=saage", *args],
                   check=False)


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--candidate", type=float, required=True)
    ap.add_argument("--best", type=float, required=True)
    ap.add_argument("--failures", type=int, default=0)
    ap.add_argument("--lower-is-better", default="false")
    args = ap.parse_args()

    cand, best, fails = args.candidate, args.best, args.failures
    lower_is_better = str(args.lower_is_better).lower() == "true"
    # strict inequality: a tie (cand == best) counts as NOT improved, so an
    # experiment that merely reproduces the current best is reverted and the
    # plateau counter advances rather than churning an equal-score commit.
    improved = (cand < best) if lower_is_better else (cand > best)

    if improved:
        git("add", "-A")
        git("commit", "-m", f"keep score {cand}")
        best, fails, status = cand, 0, "keep"
    else:
        # preserve the research log across the revert (git clean would wipe it)
        saved = open("research_log.md").read() if os.path.exists("research_log.md") else ""
        git("checkout", "--", ".")
        git("clean", "-fd")
        if saved:
            open("research_log.md", "w").write(saved)
        fails, status = fails + 1, "revert"

    with open("research_log.md", "a") as f:
        f.write(f"- candidate={cand} best={best} -> {status}\n")

    # structured per-experiment record for the final report. experiments.jsonl is
    # gitignored, so it survives the `git clean` above and accumulates across the run.
    _record_experiment(cand, best, improved)

    print(f"RESULT={status} BEST_SCORE={best} FAILURES={fails}")


def _record_experiment(candidate: float, best: float, kept: bool) -> None:
    import json
    step = 1
    if os.path.exists("experiments.jsonl"):
        step = 1 + sum(1 for _ in open("experiments.jsonl"))
    proposal = ""
    if os.path.exists("proposals/latest.md"):
        proposal = open("proposals/latest.md").read().strip()
    with open("experiments.jsonl", "a") as f:
        f.write(json.dumps({"step": step, "candidate": candidate, "best": best,
                            "kept": kept, "proposal": proposal}) + "\n")


if __name__ == "__main__":
    main()
