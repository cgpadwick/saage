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

Measured evidence for the steps that ran (exit code, wall seconds, GPU/load
averages where sampled, stderr tail on failure):
{% if step_metrics is defined %}{% for sid, m in step_metrics.items() %}- {{ sid }}: {{ m }}
{% endfor %}{% else %}(none recorded){% endif %}

Use it — reason from measurements, never invent a cause from an exit code:
- exit 124 means the step TIMED OUT and its process tree was killed. FAIL,
  say "timed out", and quote the stderr tail; do not guess OOM/crash.
- a stderr_tail is the real error — quote it in your feedback verbatim
  rather than characterizing it.
- on a cuda device, gpu_util_avg under ~20% across a long wall time is a
  hardware misfit: do NOT fail an otherwise-valid run for it, but flag it
  prominently in your summary so the perf reviewer and proposer see it.

When failing, summarize the actual error (stderr tail first, then the log)
in 1-3 lines (this is re-injected as feedback for the retry).

End your reply with `ACTION: pass` or `ACTION: fail`.
