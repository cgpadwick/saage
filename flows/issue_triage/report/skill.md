---
name: report
description: "Write the triage report: root cause, evidence, suggested fix."
tools: [read_file, write_file]
---
SKILL_ID: report

Your ONLY deliverable is `triage_report.md`. Verification already happened
upstream (the repro loop ran the repro; its verdict is in the record) — your
job is synthesis, not further investigation. Read `triage_notes.md`,
`triage_issue.json`, `repro_saage.py`, and at most the one or two source
files the notes point at. Then write `triage_report.md`:

1. **Issue** — one-paragraph restatement (number + title).
2. **Root cause** — the mechanism, with `path:line` references and the
   offending code quoted (from the notes and your reads).
3. **Reproduction** — verified (the repro loop passed) or explicitly
   "unreproduced" (the loop exhausted its attempts — then present the root
   cause as hypothesis and say what blocked reproduction).
4. **Suggested fix** — a unified diff in a ```diff block, marked as
   UNVALIDATED — proposed from analysis, not executed. Do NOT apply it.
5. **Risks / notes** — affected callers, edge cases, anything unverified.

Be explicit about verified vs hypothesized throughout. You write only
`triage_report.md` — never project files. If feedback from a previous
attempt is provided, your first action is `write_file` of the complete
report; only refine after the file exists.
