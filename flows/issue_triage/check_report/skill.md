---
name: check_report
description: "Check that triage_report.md exists and is complete."
tools: [read_file]
---
SKILL_ID: check_report

Read `triage_report.md`. If the file does not exist, that is an automatic
fail — tell the writer its ONLY job is to write triage_report.md now, from
what it already knows, before any further investigation.

If it exists, it passes when it has real content (not placeholders) for:
the issue restatement, a root cause with `path:line` references, the
reproduction status (verified or explicitly unreproduced), a suggested fix
as a diff block, and risks/notes.

End your reply with exactly one line:
`ACTION: pass` or `ACTION: fail` (with what is missing — this feeds back to
the writer). No ACTION line counts as fail.
