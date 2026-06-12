#!/usr/bin/env python3
"""Worker-side setup for one batched experiment (cwd = the worker's clone).

Two things the clone is missing:
  1. the coordinator's `.git/info/exclude` entries — info/exclude does not
     travel with a clone/bundle, and without it `git add -A` in the patch
     step would stage the data link, checkpoints, ledgers...
  2. the competition data — linked from the node cache, where the round's
     provision step staged it ($SAAGE_CACHE/datasets/mlebench/<comp>/public).

Prints DATA_READY=1 on success; exits 1 (failing the run -> nan score)
when the cache has no data for this competition.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from setup_competition import EXCLUDES  # the coordinator's exclude list

# batch-run outputs that must never ride experiment.patch
WORKER_EXCLUDES = [*EXCLUDES, "experiment.patch", "proposal.md",
                   "eval_results.json", "training.log"]


def main() -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--comp", required=True)
    args = ap.parse_args()

    exclude = Path(".git/info/exclude")
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    with exclude.open("a") as f:
        for pat in WORKER_EXCLUDES:
            if pat not in existing:
                f.write(pat + "\n")

    cache = os.environ.get("SAAGE_CACHE", "")
    src = Path(cache) / "datasets" / "mlebench" / args.comp / "public"
    if not cache or not src.is_dir():
        print(f"ERROR: no competition data at {src} — provision did not run "
              f"or used a different layout", file=sys.stderr)
        print("DATA_READY=0")
        return 1
    if not Path("data").exists():
        os.symlink(src, "data")
    n = sum(1 for _ in src.iterdir())
    print(f"data -> {src} ({n} entries)")
    print("DATA_READY=1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
