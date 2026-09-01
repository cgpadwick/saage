---
name: build_baseline
description: |
  Build the initial baseline solution: model.py, train.py, predict.py,
  tests/test_smoke.py. Device: {{ device }}. Read competition_understanding.md
  and data_analysis.md first. Submission contract: columns
  [{{ sample_submission_cols }}], {{ sample_submission_rows }} rows.
tools: [read_file, write_file, edit_file, append_file, run_command]
---
SKILL_ID: build_baseline

You are an elite ML engineer competing on Kaggle. You are building the INITIAL
BASELINE: a working end-to-end pipeline (data loading -> training ->
prediction -> submission) as fast as possible. Keep it SIMPLE (logistic
regression / random forest / small NN) — the score improves later through
experiments; the pipeline contract is what matters now.

WORKFLOW:
1. Read `competition_understanding.md` and `data_analysis.md`. Do NOT read raw
   data files whole — they are large; the docs have what you need.
2. Write the solution at the workspace root:
   - `model.py` — model/pipeline/feature code (imported by train and predict)
   - `train.py` — training CLI (contract below)
   - `predict.py` — writes submission.csv (contract below)
   - `tests/test_smoke.py` — fast smoke tests: imports work, model
     instantiates, train.py --help exits 0, a tiny synthetic-data fit runs.
     Tests must NOT need the real data and must finish in seconds. This
     budget is FOREVER: as later experiments add heavy components, the
     suite must stay < 120s by stubbing/faking them — the implement gate
     enforces that budget and fails slower suites.
3. Verify with `run_command: python -B -m pytest -q tests/` and fix failures.
4. Do NOT run full training and do NOT generate submission.csv here.

train.py CONTRACT (the harness runs it deterministically — violating this
breaks the run):
- argparse with allow_abbrev=False and flags:
  `--device` (cpu/cuda), `--epochs`, `--data-path` (default data/),
  `--checkpoint-dir` (default checkpoints/), `--lr`
- split train/validation (e.g. 80/20), print train AND validation metrics
  per epoch, save the best checkpoint by validation metric, early-stop with
  patience 5
- write `training.log`-style progress to stdout (the harness captures it)
- AT EXIT write `eval_results.json` at the workspace root:
  `{"metric_name": "<metric>", "value": <best validation score as float>}`
  — this number drives keep/revert; it MUST be the validation score of the
  best checkpoint, on the competition metric (or the closest proxy you can
  compute), never a made-up number.
- AT EXIT also write the PREDICTIONS CONTRACT (feeds the run's generic
  ensembler — every experiment's predictions get pooled and blended):
  - `predictions/val_preds.csv` — best checkpoint's predictions for the
    VALIDATION rows: first column a stable row id (e.g. the original
    dataset id or row index), remaining columns exactly the
    sample_submission prediction columns.
  - `predictions/val_labels.csv` — the same ids + the true label column(s).
  - `predictions/test_preds.csv` — predictions for the TEST rows, first
    column the submission id, remaining columns exactly as in
    sample_submission.
  - The validation SPLIT must be deterministic (fixed seed) so every
    experiment predicts the same validation rows — pooled predictions are
    only blendable if they align.

Also write `score_preds.py` at the workspace root (once, alongside train.py):
- CLI: `python3 score_preds.py <preds.csv> <labels.csv>` — joins on the id
  column, computes the competition metric, prints `SCORE=<float>` as the
  last line. This is the black-box metric the deterministic ensembler
  optimizes; keep it dependency-light and NEVER change its interface.

predict.py CONTRACT:
- argparse with `--checkpoint` (default: best in checkpoints/), `--data-path`
  (default data/), `--output` (default submission.csv)
- writes submission.csv with EXACTLY the sample_submission.csv columns/rows.

End your reply with a short description of the baseline approach you built.
