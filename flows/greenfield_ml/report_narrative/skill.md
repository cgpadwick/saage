---
name: report_narrative
description: |
  Task: {{ task }}
  Write the prose summary for this ML auto-research run's final report.
tools: [read_file, write_file, run_command]
---
SKILL_ID: report_narrative

You are writing the narrative for an ML auto-research report. First gather the facts:
- `research_log.md` and `experiments.jsonl` — the experiment history. Each record has a
  `candidate` score, the running `best`, `kept` (true = it IMPROVED the score and was
  committed; false = reverted), and the `proposal` text.
- the final `model.py` (the best architecture that survived) and, if useful, `train.py`.
- `git log --oneline` shows the committed (kept) experiments.

Then write `report_narrative.md` in markdown (use `##` headings, paragraphs, `-` bullets,
`**bold**`). Be ACCURATE — use the real scores and only the experiments that actually
happened; do not invent results. Include:

## How the final model works
One clear paragraph in plain language: what kind of network it is, its key components
(layers/blocks), how an input image flows through to a prediction, and roughly how big it
is (parameter count if you can tell).

## Winning experiments
The experiments that were KEPT (improved test accuracy), in order. For each: one sentence
on what the change was and why it helped, with the score it reached (e.g. baseline 0.91 →
0.93). Briefly note notable ideas that were tried and reverted, and why a fixed-budget
hill-climb might reject them.

## Key innovations & takeaways
2–4 bullets on the techniques that drove the gains and what they teach about this task.

Write ONLY to `report_narrative.md`. Finish with a one-line confirmation.
