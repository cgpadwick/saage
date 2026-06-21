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

The per-experiment ledger (experiments.jsonl) anchors each row to the ACTUAL
change: `files_changed` (the implement step's footprint, captured before the
commit/revert) and, for kept experiments, `commit_sha` (`git rev-parse HEAD`).
That makes the record reflect what was *done*, not just the *proposal* — so a
no-op or divergent implement (empty files_changed, or a sha == parent) is
visible instead of being silently logged as a real experiment.

Invoked by the flow's keep_or_revert command:
    python3 "{flow_dir}/keep_or_revert.py" --candidate {candidate_score} \
        --best {best_score} --failures {consecutive_failures} \
        --lower-is-better {lower_is_better}
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess

# harness bookkeeping files — not part of an experiment's code footprint
_LEDGER_FILES = {"research_log.md", "experiments.jsonl"}


def git(*args: str) -> None:
    # -c identity keeps commits working even without global git config
    subprocess.run(["git", "-c", "user.email=saage@local", "-c", "user.name=saage", *args],
                   check=False)


def git_out(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip()


def _changed_files() -> list[str]:
    """The experiment's code footprint = working-tree changes vs the last kept
    commit, minus harness bookkeeping. Captured BEFORE commit/revert so a
    reverted experiment still records what it tried. Uses `diff --name-only`
    (tracked edits) + `ls-files --others` (new files) for clean paths."""
    tracked = git_out("diff", "--name-only", "HEAD").splitlines()
    untracked = git_out("ls-files", "--others", "--exclude-standard").splitlines()
    files = set()
    for path in tracked + untracked:
        path = path.strip()
        if not path or path in _LEDGER_FILES or path.startswith("proposals/"):
            continue
        files.add(path)
    return sorted(files)


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

    # capture the implement footprint AND the proposal BEFORE we commit or
    # revert — a revert's `git clean` wipes the untracked proposals/ dir
    files_changed = _changed_files()
    proposal = _read_proposal()

    if improved:
        git("add", "-A")
        git("commit", "-m", f"keep score {cand}")
        commit_sha = git_out("rev-parse", "HEAD") or None
        best, fails, status = cand, 0, "keep"
    else:
        # preserve the research log across the revert (git clean would wipe it)
        saved = open("research_log.md").read() if os.path.exists("research_log.md") else ""
        git("checkout", "--", ".")
        git("clean", "-fd")
        if saved:
            open("research_log.md", "w").write(saved)
        commit_sha = None                       # reverted: nothing committed
        fails, status = fails + 1, "revert"

    # Rich per-experiment record: BOTH the report's experiments.jsonl row AND
    # the research_log.md entry the next propose/critic agent reads — proposal
    # text + what changed + outcome — so they see what was already tried and
    # build on it instead of repeating (the sparse `- candidate=.. -> revert`
    # log gave the proposer nothing to reason about).
    _record_experiment(cand, best, improved, commit_sha, files_changed, proposal)

    print(f"RESULT={status} BEST_SCORE={best} FAILURES={fails}")


def _read_proposal() -> str:
    p = "proposals/latest.md"
    return open(p).read().strip() if os.path.exists(p) else ""


# proposals can be long; cap what goes into research_log so the proposer's read
# stays bounded while still carrying the hypothesis + the concrete change
_PROPOSAL_LOG_CHARS = 2000


def _append_research_log(step: int, candidate: float, best: float, kept: bool,
                         commit_sha: str | None, files_changed: list[str],
                         proposal: str) -> None:
    """Append a rich entry the next propose/critic agent reads: proposal text +
    the files actually changed + the outcome. Mirrors the final report's
    per-experiment row so the agents reason over real history, not bare scores."""
    result = "KEPT ✅" if kept else "reverted ❌"
    files = ", ".join(files_changed) or "none"
    sha = (commit_sha or "")[:8]
    prop = proposal or "(no proposal recorded)"
    if len(prop) > _PROPOSAL_LOG_CHARS:
        prop = prop[:_PROPOSAL_LOG_CHARS].rstrip() + "\n…(proposal truncated)"
    with open("research_log.md", "a") as f:
        f.write(
            f"\n## Experiment {step} — {result} "
            f"(candidate={candidate:.4f}, best={best:.4f})\n"
            f"- files changed: {files}\n"
            + (f"- commit: {sha}\n" if sha else "")
            + f"\n{prop}\n"
        )


def _record_experiment(candidate: float, best: float, kept: bool,
                       commit_sha: str | None, files_changed: list[str],
                       proposal: str) -> None:
    rows = []
    if os.path.exists("experiments.jsonl"):
        with open("experiments.jsonl") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    step = len(rows) + 1
    # parent_step = the most recent KEPT step (the experiment this one branched
    # off), so the report can render the experiment tree. 0 = the baseline.
    parent_step = next((r["step"] for r in reversed(rows) if r.get("kept")), 0)
    _append_research_log(step, candidate, best, kept, commit_sha,
                         files_changed, proposal)
    with open("experiments.jsonl", "a") as f:
        f.write(json.dumps({
            "step": step,
            "parent_step": parent_step,
            "candidate": candidate,
            "best": best,
            "kept": kept,
            "commit_sha": commit_sha,
            "files_changed": files_changed,
            "proposal": proposal,
        }) + "\n")


if __name__ == "__main__":
    main()
