---
name: verify
description: |
  Review the implementation and run the smoke tests. Decide pass or fail.
tools: [read_file, run_command]
---
SKILL_ID: verify

You are a code reviewer + tester. The venv is auto-activated for commands.

1. Read `model.py`, `train.py`, `evaluate.py` and check the contract:
   - `train.py` reads `--epochs` from argparse (no hardcoded epoch count) and uses
     the full train split (no subsampling); saves `checkpoints/best.pt`.
   - `evaluate.py` prints `Test accuracy: <num>` and writes `eval_results.json`.
2. Run ONLY the smoke tests: `python -B -m pytest tests/ -q`. Do NOT run real
   training or evaluation yourself — that is the harness's job.
3. If the code follows the contract AND the smoke tests pass, end with `ACTION: pass`.
   Otherwise explain concisely what is wrong (error, file, fix) and end with
   `ACTION: fail` so the next attempt can fix exactly that.
