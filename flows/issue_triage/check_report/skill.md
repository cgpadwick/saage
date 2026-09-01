---
name: check_report
description: "Check that triage_report.html exists and is complete."
tools: [read_file]
---
SKILL_ID: check_report

Read `triage_report.html`. If the file does not exist, that is an automatic
fail — tell the writer its ONLY job is to write triage_report.html now, from
what it already knows, before any further investigation.

If it exists, it passes when:
- it is a self-contained HTML document (inline style, no external assets),
- it OPENS with an executive summary table carrying real content (not
  placeholders) for: Issue, The bug, Reproduced (✅/❌ matching the actual
  repro-loop outcome), Root cause with a `path:line`, Suggested fix,
  Effort (one of XS/S/M/L/XL with justification), Confidence,
- the full sections follow: issue restatement, root cause with `path:line`
  references, reproduction status, a suggested-fix diff in a `<pre>` block
  marked UNVALIDATED, and risks/notes.

End your reply with exactly one line:
`ACTION: pass` or `ACTION: fail` (with what is missing — this feeds back to
the writer). No ACTION line counts as fail.
