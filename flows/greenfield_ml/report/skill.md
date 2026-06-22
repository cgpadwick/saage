---
name: report
description: |
  Task: {{ task }}
  Final best test accuracy: {{ best_score }} (target {{ target_accuracy }}, higher is better).
  Write the final HTML research report for this ML auto-research run.
tools: [read_file, write_file, run_command]
---
SKILL_ID: report

You are an excellent scientific report writer. Generate a beautiful, concise, and
informative scientific report as a single self-contained `report.html` from the
inputs below. Be ACCURATE — use only the real scores and experiments from the
files; never invent results.

## Inputs (read these first)
- `experiments.jsonl` — one experiment per line. Fields: `step`, `parent_step`,
  `candidate` (this experiment's test accuracy), `best` (running best after it),
  `kept` (true = it improved the score and was committed; false = reverted),
  `commit_sha`, `files_changed`, `summary` (one-paragraph change summary),
  `proposal` (full proposal text).
- `research_log.md` — the running narrative of the run.
- the final `model.py` (the best architecture that survived) and `train.py` — the
  details of what ended up working. `git log --oneline` lists the kept commits.

## The report (`report.html`) must contain, IN THIS ORDER

1. **Outcome — up front.** A short prose section naming the winning result: the
   final best accuracy vs the baseline and the target ({{ target_accuracy }}), and
   a clear description of the winning experiment(s) — the architecture/approach and
   key details that ended up working (read `model.py` for the REAL architecture).

2. **Experiment table.** One row per experiment: step, a short description of the
   change (use `summary`), candidate vs best accuracy, KEPT or REVERTED, short commit.

3. **Hill-climb graph — an inline `<svg>`** (no external libraries):
   - X axis = experiment number, Y axis = test accuracy.
   - Plot best-so-far as a line; mark each experiment: **keeps = green filled
     dots, reverts = red ✕ marks** (a red X — two crossed red lines — a distinct
     shape, not just color).
   - Include axis ticks, light gridlines, a legend (green dot = kept, red ✕ =
     reverted), and a title.
   - **Annotate selectively** — do NOT label every point (keep it uncluttered):
     call out the biggest win(s) and several notable failures with the experiment
     number + a short description (from `summary`) on a small `<text>` label with a
     thin leader line. Aim for ~4–8 annotations total.
   - Skip any experiment whose `candidate` is missing/None/nan or an off-scale
     failure sentinel (e.g. a negative score) from the chart markers and line —
     those are failed train/eval runs, not real data points (the best-so-far line
     is unaffected). An empty ledger still yields a valid (if sparse) report.

## Style
Beautiful and professional but concise. Self-contained: inline CSS + inline SVG
ONLY — no CDNs, no external files, no `<img>` to disk. The file MUST render fully
offline. Clean readable layout (headings, a styled table, the chart).

Write ONLY `report.html`. Finish with a one-line confirmation.
