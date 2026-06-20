---
name: report_narrative
description: |
  Write report_narrative.md — the research narrative of this competition run
  (best validation score: {{ best_score }}).
tools: [read_file, write_file, run_command]
---
SKILL_ID: report_narrative

Write the run's research narrative as `report_narrative.md`, for a reader who
wants to understand what was tried and what worked.

Sources: `research_log.md`, `experiments.jsonl`, `competition_understanding.md`,
`data_analysis.md`, `git log --oneline` (kept experiments), `eval_results.json`.

Structure:
1. **The competition** — task, metric (and direction), data in one paragraph.
2. **Approach** — the baseline and why.
3. **Experiment narrative** — what was proposed, kept, reverted; the
   reasoning thread, not just a list. Reference real scores.
4. **Final solution** — what the submitted model is, its validation score,
   and the final-training outcome.
5. **What we'd try next** — 2-3 concrete ideas grounded in the log.

Keep it honest: reverted experiments and failures are part of the story.

End your reply with a one-line abstract of the run.
