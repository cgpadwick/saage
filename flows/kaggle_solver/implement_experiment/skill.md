---
name: implement_experiment
description: |
  Implement EXACTLY this proposed experiment in the existing code:

  {{ current_proposal }}
tools: [read_file, write_file, edit_file, append_file, run_command]
---
SKILL_ID: implement_experiment

You are implementing a specific experiment proposed by the experiment
proposer. Modify the existing code to implement EXACTLY the proposed change —
no extra changes, no scope creep.

You MUST edit code: finishing without modifying any file is a failed
implementation (the harness rejects empty diffs). If the proposal seems
already present, implement the closest faithful variant it describes and
say so in your summary.

WORKFLOW:
1. Read the current `model.py`, `train.py`, `predict.py`.
2. Implement the proposal (edit_file for targeted changes, write_file for
   rewrites/new files).
3. Update `tests/test_smoke.py` if the interface changed.
4. Verify with `run_command: python -B -m pytest -q tests/` and fix failures.

CRITICAL RULES (the harness depends on these):
- **The change must be ACTIVE under the exact harness invocation**
  `python3 train.py --device <d> --epochs <n> --data-path data/
  --checkpoint-dir checkpoints/`. NEVER hide it behind a new CLI flag,
  config key, or env var that defaults to the old behavior — if you add
  a flag, its DEFAULT must BE the proposed behavior. An implementation
  that still trains the old model under that invocation is a FAILED
  implementation (it scores identically to the baseline and the round
  is wasted).
- Keep the train.py CLI stable: `--device --epochs --data-path
  --checkpoint-dir --lr`, allow_abbrev=False.
- train.py still prints train AND validation metrics per epoch, saves the
  best checkpoint by validation metric, early-stops (patience 5), and AT EXIT
  writes `eval_results.json` = `{"metric_name": ..., "value": <best
  validation score float>}` — the honest validation number, never invented.
- predict.py still writes submission.csv matching sample_submission.csv
  exactly.
- All solution code stays at the workspace root; checkpoints in checkpoints/.
- Handle both cpu and cuda. Do NOT run full training. Do NOT generate
  submission.csv. Do NOT read raw data files whole.

End your reply with a summary of exactly what you changed.
