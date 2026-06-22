#!/usr/bin/env python3
"""One-time setup for the le-wm hill-climb (deterministic — no LLM).

Runs with cwd = the le-wm repo (the flow workspace). It:
  1. sanity-checks this really is the le-wm repo,
  2. adds run-artifact patterns to .git/info/exclude so `git add -A` /
     `git clean -fd` in keep_or_revert never touch checkpoints, hydra outputs,
     or the experiment ledger,
  3. switches to (or creates) the experiment branch (`--branch`; default
     `saage-hillclimb` locally — remote runs pass the handoff's run branch so
     kept-experiment commits land on the branch the node pushes back) and
     commits the current tracked changes as a snapshot, so every experiment
     diffs against a known-good state,
  4. stages `clean_ckpt.py` into the workspace as `saage_clean_ckpt.py` so the
     verify agent can clean the smoke-run checkpoint dir by relative path,
  5. seeds `research_log.md` with the context the proposer needs (paper target,
     prior manual results, the fixed budget).

Prints `SETUP=ok BRANCH=<name>` on success.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_BRANCH = "saage-hillclimb"

# Heavy / generated artifacts that live inside the repo working tree. Excluding
# them (rather than .gitignore) keeps the user's tree untouched while making the
# flow's `git add -A` + `git clean -fd` safe.
EXCLUDES = [
    "outputs/",
    "lightning_logs/",
    "__pycache__/",
    "lewm/",
    "lewm_cube/",
    "lewm_reacher/",
    "environment*.json",
    "requirements_frozen*.txt",
    "experiments.jsonl",
    "proposals/",
    "saage_clean_ckpt.py",
    "saage_autoresearch_ideas.md",
    ".venv/",
]

LOG_HEADER = """# LeWM OGBench-Cube hill-climb research log

Goal: raise planning success_rate on OGBench-Cube (eval.py, 50 episodes, seed 42,
CEM solver.n_steps=10 — the paper's cube planning budget) toward the paper's LeWM
number: **{target}%** (LeWorldModel paper, Fig. 6; baselines there: PLDM 65,
DINO-WM 86, random 48).

THE PAPER'S CUBE RECIPE (App. D/E + ablations App. G — verified against the
official HF `quentinll/lewm-cube` checkpoint config, which matches this repo's
`config/train/model/lewm.yaml` exactly):
- batch_size 128, **10 epochs total**, AdamW lr 5e-5 wd 1e-3, bf16, grad-clip 1.0
- SIGReg lambda = 0.09 (Fig. 16: the tuned PEAK; 0.01-0.2 all work, 0.5 collapses)
- predictor dropout 0.1 (Tab. 9 optimum: 0.0 -> 78, 0.1 -> 96, 0.2 -> 85 on PushT)
- frameskip 5, history 3, img 224, embed_dim 192 (saturates above ~184, Fig. 15)
- planning: CEM 300 samples, 10 iterations (cube), horizon 5, MPC
Ablation levers worth knowing (PushT numbers): predictor size tiny 80.7 /
small 96.0 / base 86.7 (Tab. 6) -> predictor CAPACITY is a promising lever;
SIGReg projections/knots are insensitive (don't bother).

Prior local results (measured under the OLD eval protocol, n_steps=30 — NOT
directly comparable to scores below):
- A batch-256 run stopped at epoch 54/100; `lewm_cube/weights_epoch_54.pt`
  scored 60-64% over three evals.
- Deviations from the paper recipe found and FIXED before the baseline:
  batch_size 256 -> 128 (a local bump, lr was never rescaled), eval CEM
  n_steps 30 -> 10 (the 30 is the paper's PushT setting).

Budget note: ~2.5 h/epoch here. The budget is FIXED at {epochs} epochs per
experiment (the paper trains 10), so prefer changes that help in a SHORT
training budget; the final winner deserves a 10-epoch confirmation run.

Every experiment below trains exactly {epochs} epochs and is evaluated with the
frozen protocol. `keep` = improved the best score (committed); `revert` = did not.

## Experiments
"""


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def git(*args: str) -> subprocess.CompletedProcess:
    return sh("git", "-c", "user.email=saage@local", "-c", "user.name=saage", *args)


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--train-epochs", type=int, required=True)
    ap.add_argument("--target", type=float, required=True)
    ap.add_argument("--branch", default=DEFAULT_BRANCH,
                    help="experiment branch (remote runs pass the run branch)")
    args = ap.parse_args()
    branch = args.branch or DEFAULT_BRANCH   # templated arg may render empty

    if not (Path("train.py").exists() and Path("jepa.py").exists()
            and Path(".git").exists()):
        print("ERROR: cwd does not look like the le-wm repo", file=sys.stderr)
        sys.exit(1)

    exclude = Path(".git/info/exclude")
    existing = exclude.read_text() if exclude.exists() else ""
    with exclude.open("a") as f:
        for pat in EXCLUDES:
            if pat not in existing:
                f.write(pat + "\n")

    current = sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if current != branch:
        exists = sh("git", "rev-parse", "--verify", "--quiet", branch).returncode == 0
        r = git("checkout", branch) if exists else git("checkout", "-b", branch)
        if r.returncode != 0:
            print(f"ERROR: could not switch to {branch}: {r.stderr}", file=sys.stderr)
            sys.exit(1)

    shutil.copy(Path(__file__).parent / "clean_ckpt.py", "saage_clean_ckpt.py")
    # the guided-proposal menu (read by the propose skill each iteration)
    shutil.copy(Path(__file__).parent / "autoresearch_ideas.md",
                "saage_autoresearch_ideas.md")

    # Reset the ledger so each run starts clean. experiments.jsonl and
    # research_log.md are git-excluded, so on a REUSED workspace (le-wm is a
    # persistent repo) they would otherwise carry a prior run's rows into this
    # run's report AND the proposer's context — the non-monotonic "best" you get
    # when two runs concatenate. Setup runs once per fresh run; a resumed run
    # skips already-completed steps, so its in-progress ledger is preserved.
    Path("research_log.md").write_text(
        LOG_HEADER.format(target=args.target, epochs=args.train_epochs))
    Path("experiments.jsonl").unlink(missing_ok=True)

    # snapshot tracked changes (+ the research log) so experiments diff cleanly
    git("add", "-u")
    git("add", "research_log.md")
    git("commit", "-m", "saage: hillclimb setup snapshot")

    print(f"SETUP=ok BRANCH={branch}")


if __name__ == "__main__":
    main()
