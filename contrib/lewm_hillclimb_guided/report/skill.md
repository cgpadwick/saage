---
name: report
description: |
  Task: {{ task }}
  Best VALIDATION success_rate (split=val, used for hill-climb selection): {{ best_score }}.
  HEADLINE held-out TEST success_rate (split=test, winner retrained, evaluated ONCE): {{ confirm_score }}.
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
  All `candidate`/`best` scores in the ledger are VALIDATION (split=val) — they
  drove selection only.
- `research_log.md` — the goal, the paper's reference numbers, and the run narrative.
  Its `CONFIRMATION:` line carries the HELD-OUT TEST success_rate ({{ confirm_score }},
  split=test) — the honest headline number, with no selection bias.
- `config/train/lewm.yaml` and `config/train/model/lewm.yaml` — the winning
  configuration that survived (the details of what worked). `git_log` lists the
  kept commits (`saage: keep experiment, ...`).

## The report (`report.html`) must contain, IN THIS ORDER

1. **Outcome — up front.** A short prose section naming the winning result. Lead with
   the **held-out TEST success_rate ({{ confirm_score }})** as the headline number, and
   state plainly that selection used a disjoint VALIDATION split so this carries no
   test-set selection bias. Then give the baseline success_rate, the best VALIDATION
   score vs the target ({{ target_success }}), and a clear description of the winning
   experiment(s) — the configuration/approach and key details that ended up working
   (read the two config YAMLs for the REAL winning settings).

2. **Experiment table.** One row per experiment: step, a short description of the
   change (use `summary`), candidate vs best **validation** success_rate, KEPT or
   REVERTED, short commit. (These are all val scores; note the test headline above.)

3. **Hill-climb graph — an inline `<svg>`** (no external libraries):
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
