#!/usr/bin/env python3
"""Seed the coordinator workspace: a git repo with the baseline train.py.
Idempotent — re-running on an existing workspace is a no-op. cwd = workspace.
Prints READY=1 for the flow."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

FLOW_DIR = Path(__file__).resolve().parent

GITIGNORE = """\
__pycache__/
data/
eval_results.json
experiment.patch
training.log
proposal.md
proposals/
batch/
rounds.jsonl
research_log.md
"""


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def main() -> None:
    ws = Path.cwd()
    if (ws / ".git").exists() and (ws / "train.py").exists():
        print("workspace already seeded")
        print("READY=1")
        return
    if not (ws / ".git").exists():
        git("init", "-q", "-b", "main")
    shutil.copyfile(FLOW_DIR / "seed" / "train.py", ws / "train.py")
    (ws / ".gitignore").write_text(GITIGNORE)
    (ws / "research_log.md").write_text(
        "# Fashion-MNIST batched hill-climb — research log\n\n"
        "Baseline: 2-layer MLP (784-256-10), Adam 1e-3, batch 128, "
        "no augmentation.\n")
    git("add", "-A")
    git("-c", "user.email=saage@local", "-c", "user.name=saage",
        "commit", "-q", "-m", "baseline: MLP on Fashion-MNIST")
    print("workspace seeded with baseline")
    print("READY=1")


if __name__ == "__main__":
    sys.exit(main())
