---
name: report
description: |
  Competition: {{ competition_id }}
  Metric: {{ metric_name }} ({{ "lower is better" if lower_is_better else "higher is better" }}).
  Final best validation score: {{ best_score }} (target {{ target_score }}).
  Write the final HTML report for this kaggle-solver run.
tools: [read_file, write_file, run_command, git_log]
---
SKILL_ID: report

You are an excellent scientific report writer. Generate a beautiful, concise, and
informative scientific report as a single self-contained `report.html` from the
inputs below. Be ACCURATE — use only the real scores and experiments from the
files; never invent results.

## Inputs (read these first)
- `experiments.jsonl` — one experiment per line. Fields: `step`, `parent_step`,
  `candidate` (this experiment's validation score; may be `null` for a failed
  train/eval), `best` (running best after it), `kept` (true = it improved the
  score and was committed; false = reverted), `commit_sha`, `files_changed`,
  `summary` (one-paragraph change summary), `proposal` (full proposal text).
- `research_log.md` — the competition goal, metric, and the run narrative.
- the winning solution files — `model.py`, `train.py`, `predict.py` (the approach
  that survived) — and `submission.csv` (the final submission). `git_log` lists
  the kept commits.

## The report (`report.html`) must contain, IN THIS ORDER

1. **Outcome — up front.** A short prose section naming the winning result: the
   final best {{ metric_name }} vs the baseline and the target ({{ target_score }}),
   and a clear description of the winning experiment(s) — the modelling approach /
   feature engineering and key details that ended up working (read `model.py` /
   `train.py` for the REAL solution).

2. **Experiment table.** One row per experiment: step, a short description of the
   change (use `summary`), candidate vs best {{ metric_name }}, KEPT or REVERTED,
   short commit.

3. **Hill-climb graph — an inline `<svg>`** (no external libraries):
   - X axis = experiment number, Y axis = {{ metric_name }}. NOTE the metric range
     is arbitrary (not necessarily 0–1) and **{{ "lower is better" if lower_is_better else "higher is better" }}** — scale the Y axis to the data's
     actual range and orient the best-so-far line in the improving direction.
   - Plot best-so-far as a line; mark each experiment: **keeps = green filled
     dots, reverts = red ✕ marks** (a red X — two crossed red lines — a distinct
     shape, not just color).
   - Include axis ticks, light gridlines, a legend (green dot = kept, red ✕ =
     reverted), and a title.
   - **Annotate selectively** — do NOT label every point (keep it uncluttered):
     call out the biggest win(s) and several notable failures with the experiment
     number + a short description (from `summary`) on a small `<text>` label with a
     thin leader line. Aim for ~4–8 annotations total.
   - Skip any experiment whose `candidate` is `null`/missing from the chart markers
     and line — those are failed train/eval runs, not real data points (the
     best-so-far line is unaffected). An empty ledger still yields a valid report.

## Style
Beautiful and professional but concise. Self-contained: inline CSS + inline SVG
ONLY — no CDNs, no external files, no `<img>` to disk. The file MUST render fully
offline. Clean readable layout (headings, a styled table, the chart).

Write ONLY `report.html`. Finish with a one-line confirmation.
