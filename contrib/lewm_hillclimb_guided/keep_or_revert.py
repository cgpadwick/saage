#!/usr/bin/env python3
"""Keep or revert a le-wm hill-climb experiment (deterministic — no LLM).

Runs with cwd = the le-wm repo (the flow workspace). Compares the candidate
success_rate to the current best:

  improved (or --baseline true)
      -> promote the experiment checkpoint dir to the best-checkpoint dir
         ($STABLEWM_HOME/checkpoints/<best-name>), git-commit the code change,
         advance best, reset the failure counter.
  not improved
      -> git checkout/clean back to the last kept commit (preserving
         research_log.md, which the revert would otherwise roll back), and
         increment the failure counter.

A candidate of -1 (train or eval crashed — the flow pre-seeds -1 before each
attempt) always reverts, except on the baseline, where it is recorded so the
run can continue and the first improving experiment becomes the new best.

Prints `RESULT=... BEST_SCORE=... FAILURES=...` for the flow's `set:` captures.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def git(*args: str) -> None:
    # -c identity keeps commits working even without global git config
    subprocess.run(["git", "-c", "user.email=saage@local", "-c", "user.name=saage", *args],
                   check=False)


def git_out(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip()


_LEDGER_FILES = {"research_log.md", "experiments.jsonl", "eval_results.json"}


def _changed_files() -> list[str]:
    """The experiment's code footprint vs the last kept commit, minus harness
    bookkeeping. Captured BEFORE commit/revert so a reverted attempt still
    records what it tried."""
    tracked = git_out("diff", "--name-only", "HEAD").splitlines()
    untracked = git_out("ls-files", "--others", "--exclude-standard").splitlines()
    files = set()
    for path in tracked + untracked:
        path = path.strip()
        if not path or path in _LEDGER_FILES or path.startswith("proposals/"):
            continue
        files.add(path)
    return sorted(files)


def cache_dir() -> Path:
    return Path(os.environ.get("STABLEWM_HOME", Path.home() / ".stable-wm"))


def promote(exp: str, best_name: str) -> bool:
    """Copy checkpoints/<exp> -> checkpoints/<best_name>; False if exp missing."""
    src = cache_dir() / "checkpoints" / exp
    dst = cache_dir() / "checkpoints" / best_name
    if not src.is_dir():
        return False
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--candidate", type=float, required=True)
    ap.add_argument("--best", type=float, required=True)
    ap.add_argument("--failures", type=int, default=0)
    ap.add_argument("--exp", required=True)
    ap.add_argument("--best-name", required=True)
    ap.add_argument("--baseline", default="false")
    args = ap.parse_args()

    cand, best, fails = args.candidate, args.best, args.failures
    baseline = str(args.baseline).lower() == "true"
    # strict inequality: a tie merely reproduces the best, so it reverts and
    # the plateau counter advances rather than churning an equal-score commit.
    improved = baseline or cand > best

    if improved:
        promoted = promote(args.exp, args.best_name)
        if not promoted and not baseline:
            improved = False    # train never produced a checkpoint -> treat as failed

    # capture the implement footprint, the proposal, and its one-paragraph
    # summary BEFORE we commit or revert — a revert's `git clean` wipes the
    # untracked proposals/ dir
    files_changed = _changed_files()
    proposal = _read_proposal()
    summary = _read_summary()

    if improved:
        git("add", "-A")
        git("commit", "-m", f"saage: keep experiment, success_rate {cand}")
        commit_sha = git_out("rev-parse", "HEAD") or None
        best, fails, status = (cand if cand > best else best), 0, "keep"
        if baseline:
            best = cand
    else:
        # preserve the research log across the revert (it is tracked, so
        # checkout would roll it back to the last kept commit)
        log = Path("research_log.md")
        saved = log.read_text() if log.exists() else ""
        git("checkout", "--", ".")
        git("clean", "-fd")
        if saved:
            log.write_text(saved)
        commit_sha = None                       # reverted: nothing committed
        fails, status = fails + 1, "revert"

    # Two records, two audiences:
    #  - research_log.md: the proposer's working memory — a ONE-paragraph summary
    #    + what changed + outcome. Kept terse so re-reading it every iteration
    #    does not blow the proposer's context.
    #  - experiments.jsonl: the human record / report.html — full proposal text,
    #    summary, files, commit. Not read by the proposer, so size is fine.
    _record_experiment(cand, best, status, commit_sha, files_changed,
                       proposal, summary)

    print(f"RESULT={status} BEST_SCORE={best} FAILURES={fails}")


def _read_proposal() -> str:
    p = "proposals/latest.md"
    return open(p).read().strip() if os.path.exists(p) else ""


def _read_summary() -> str:
    """The summarize agent's one-paragraph digest of the proposal."""
    p = "proposals/summary.md"
    return open(p).read().strip() if os.path.exists(p) else ""


def _append_research_log(step: int, candidate: float, best: float, status: str,
                         commit_sha: str | None, files_changed: list[str],
                         summary: str) -> None:
    """Append the terse entry the next propose/critic agent reads: a one-paragraph
    change summary + the files actually changed + the outcome. The summary (not
    the full proposal) keeps this log small enough to re-read every iteration."""
    result = "KEPT ✅" if status == "keep" else "reverted ❌"
    files = ", ".join(files_changed) or "none"
    sha = (commit_sha or "")[:8]
    body = summary or "(no summary written)"
    with open("research_log.md", "a") as f:
        f.write(
            f"\n## Experiment {step} — {result} "
            f"(candidate={candidate:g}, best={best:g})\n"
            f"- changed: {files}\n"
            + (f"- commit: {sha}\n" if sha else "")
            + f"\n{body}\n"
        )


def _record_experiment(candidate: float, best: float, status: str,
                       commit_sha: str | None, files_changed: list[str],
                       proposal: str, summary: str) -> None:
    """Structured ledger for the final report. experiments.jsonl is excluded
    from git, so it survives the revert above and accumulates across the run.
    Anchors each row to the ACTUAL change (commit_sha for kept, files_changed
    for every attempt) — not just the proposal — so a no-op/divergent implement
    is visible rather than silently logged as a real experiment."""
    rows = []
    if os.path.exists("experiments.jsonl"):
        with open("experiments.jsonl") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    step = len(rows) + 1
    parent_step = next((r["step"] for r in reversed(rows)
                        if r.get("status") == "keep"), 0)
    _append_research_log(step, candidate, best, status, commit_sha,
                         files_changed, summary)
    with open("experiments.jsonl", "a") as f:
        f.write(json.dumps({"step": step, "parent_step": parent_step,
                            "candidate": candidate, "best": best, "status": status,
                            "commit_sha": commit_sha, "files_changed": files_changed,
                            "summary": summary, "proposal": proposal}) + "\n")


if __name__ == "__main__":
    main()
