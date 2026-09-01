---
name: report
description: "Write the triage report: root cause, evidence, suggested fix."
tools: [read_file, write_file, run_command]
---
SKILL_ID: report

Read `triage_notes.md`, `triage_issue.json`, `repro_saage.py`, and the
suspect source files. Then WRITE `triage_report.md` IMMEDIATELY — a complete
report from what you already know is the deliverable; a perfect analysis
with no report file is a failed step. Only after the file exists may you
spend remaining steps validating the suggested fix (e.g. in a /tmp copy,
never the workspace) and updating the report with what you verified.

`triage_report.md` structure:

1. **Issue** — one-paragraph restatement (number + title).
2. **Root cause** — the mechanism, with `path:line` references and the
   offending code quoted.
3. **Reproduction** — whether `repro_saage.py` verifiably fails for the
   issue's reason (run it if you need the current output). If the repro loop
   gave up, say "unreproduced" and present the root cause as hypothesis.
4. **Suggested fix** — a unified diff in a ```diff block. Do NOT apply it;
   the diff in the report is the deliverable. Note how the fix makes
   `repro_saage.py` pass.
5. **Risks / notes** — affected callers ({{ test_cmd }} scope), edge cases,
   anything you could not verify.

Be explicit about verified vs hypothesized throughout. You write only
`triage_report.md` — never project files.
