---
name: report_narrative
description: |
  Task: {{ task }}
  Final best success_rate: {{ best_score }} (target was {{ target_success }}).
  Write the markdown report for this hill-climb run.
tools: [read_file, write_file, run_command, git_log]
---
SKILL_ID: report_narrative

You are writing the final report for an automated hill-climb over LeWM training
on OGBench-Cube. First gather the facts:

- `research_log.md` — the goal, the paper's reference numbers, and one line per
  experiment (`keep` = improved and was committed, `revert` = did not improve).
- `experiments.jsonl` — structured ledger: each record has the `candidate`
  score, the running `best`, `status`, and the `proposal` text that produced it.
- `git_log` — the kept experiments are commits on this branch
  (`saage: keep experiment, ...`).
- The current `config/train/lewm.yaml` / `config/train/model/lewm.yaml` — the
  winning configuration that survived.

Then write `report_narrative.md` in markdown. Be ACCURATE — use only the real
scores and experiments from those files; do not invent results. Include:

## Outcome
Baseline score at the fixed {{ train_epochs }}-epoch budget, the final best
score, and how both compare to the paper's 74% target and the earlier manual
runs (60-64% at epoch 54 of a 100-epoch run).

## Winning experiments
The KEPT experiments in order: what changed, why it plausibly helped, score
before -> after. Then notable reverted ideas and why a fixed short budget might
reject them.

## Recommended next steps
2-4 bullets — e.g. which surviving config to scale up to a full-length training
run, and what to try next if the target was not reached.

Write ONLY to `report_narrative.md`. Finish with a one-line confirmation.
