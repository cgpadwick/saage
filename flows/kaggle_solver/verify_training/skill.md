---
name: verify_training
description: |
  Sanity-check the training run that just finished (captured validation
  score: {{ candidate_score }}). End with ACTION: pass or ACTION: fail.
tools: [read_file, run_command]
---
SKILL_ID: verify_training

A deterministic training command just ran (`train.py`). Decide whether it
actually trained — not whether the score is good.

1. Look at the training output: `run_command: tail -50 training.log` if it
   exists, else check `ls checkpoints/` and `cat eval_results.json`.
2. PASS if: training ran multiple epochs (or early-stopped legitimately),
   a checkpoint was saved, and eval_results.json holds a real validation
   number consistent with the log.
3. FAIL if: the run crashed, produced no checkpoint, eval_results.json is
   missing/stale/inconsistent with the log, or the metric is degenerate in a
   way that screams bug (e.g. exactly 0.0 with a loss that never moved).
   A LOW score is NOT a failure — keep/revert judges quality, you judge
   integrity.

When failing, summarize the actual error from the log in 1-3 lines (this is
re-injected as feedback for the retry).

End your reply with `ACTION: pass` or `ACTION: fail`.
