---
name: report
description: |
  Task: {{ task }}
  Best VALIDATION success_rate (val eval seed, used for hill-climb selection): {{ best_score }}.
  HEADLINE held-out TEST success_rate (separate test eval seed, winner retrained, evaluated ONCE): {{ confirm_score }}.
  Specialization gain (held-out test): {{ specialization_gain }} points
  (paper-recipe {{ paper_test_score }} -> specialized {{ confirm_score }}; DINO-WM = 86).
  Target was {{ target_success }} (higher is better).
  Write the final HTML report for this LeWM hill-climb run.
tools: [read_file, write_file, run_command, git_log]
---
SKILL_ID: report

You are an excellent scientific report writer. Generate a beautiful, concise, and
informative scientific report as a single self-contained `report.html` from the
inputs below. Be ACCURATE — use only the real scores and experiments from the
files; never invent results.

## Inputs (read these first)
- `experiments.jsonl` — one experiment per line. Fields: `step`, `parent_step`,
  `candidate` (this experiment's success_rate), `best` (running best after it),
  `status` ("keep" = it improved the score and was committed; "revert" = did not),
  `commit_sha`, `files_changed`, `summary` (one-paragraph change summary),
  `proposal` (full proposal text).
  All `candidate`/`best` scores in the ledger are VALIDATION (val eval seed) — they
  drove selection only.
- `research_log.md` — the goal, the paper's reference numbers, and the run narrative.
  Its `CONFIRMATION:` line carries the HELD-OUT TEST success_rate ({{ confirm_score }},
  a separate test eval seed) — the honest headline number, with no selection bias.
- `config/train/lewm.yaml` and `config/train/model/lewm.yaml` — the winning
  configuration that survived (the details of what worked). `git_log` lists the
  kept commits (`saage: keep experiment, ...`).

## The report (`report.html`) must contain, IN THIS ORDER

1. **Headline (one line, up front).** "Specializing LeWM on OGBench-Cube:
   paper-recipe {{ paper_test_score }}% -> specialized {{ confirm_score }}%
   ({{ specialization_gain }} pts), held-out test ({{ test_num_eval }} episodes,
   single seed). DINO-WM = 86%." Then a 1-2 sentence interpretation of the gap.

2. **Recipe diff.** A small table: paper recipe vs winning recipe (lr, lambda,
   history_size, num_preds, embed_dim, predictor size, augmentations) — read the
   REAL values from config/train/lewm.yaml and config/train/model/lewm.yaml.

3. **Honesty box (a callout).** n=1 task (cube only); single-seed headline
   (multi-seed CIs = future work); the in-loop selection metric is VALIDATION on
   50 episodes and is NOISY (+/-~7%), so the hill-climb trajectory is exploratory
   — only the held-out TEST headline is the claim. The held-out test uses a
   DIFFERENT eval seed than selection (a separate episode sample); both are drawn
   from the same pool, so a tiny (~1-2 episode) overlap is possible and negligible.

4. **Outcome and methodology.** Explain the winning configuration and approach
   that worked. State plainly that selection used a separate validation eval seed
   (the held-out TEST uses a different seed), so the headline carries no test-set
   selection bias. Give the baseline
   success_rate, compare the best VALIDATION score against the target
   ({{ target_success }}), and describe the key winning details (read
   config/train/lewm.yaml and config/train/model/lewm.yaml for the REAL settings).

5. **Experiment table.** One row per experiment: step, a short description of the
   change (use `summary`), candidate vs best **validation** success_rate, KEPT or
   REVERTED, short commit. (These are all val scores; note the test headline above.)

6. **Hill-climb graph — an inline `<svg>`** (no external libraries):
   - X axis = experiment number, Y axis = success_rate.
   - Plot best-so-far as a line; mark each experiment: **keeps (status "keep") =
     green filled dots, reverts (status "revert") = red ✕ marks** (a red X — two
     crossed red lines — a distinct shape, not just color).
   - Include axis ticks, light gridlines, a legend (green dot = kept, red ✕ =
     reverted), and a title.
   - **Annotate selectively** — do NOT label every point (keep it uncluttered):
     call out the biggest win(s) and several notable failures with the experiment
     number + a short description (from `summary`) on a small `<text>` label with a
     thin leader line. Aim for ~4–8 annotations total.
   - Skip any experiment whose `candidate` is missing/None/nan or an off-scale
     failure sentinel (the flow seeds `-1` for a crashed train/eval) from the
     chart markers and line — those are failed runs, not real data points (the
     best-so-far line is unaffected). An empty ledger still yields a valid report.

## Style
Beautiful and professional but concise. Self-contained: inline CSS + inline SVG
ONLY — no CDNs, no external files, no `<img>` to disk. The file MUST render fully
offline. Clean readable layout (headings, a styled table, the chart).

Write ONLY `report.html`. Finish with a one-line confirmation.
