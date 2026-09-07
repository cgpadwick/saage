---
name: verify_report
description: "Check every claim in report.md against sources.md."
tools: [read_file]
---
SKILL_ID: verify_report

Read `sources.md` and `report.md`. The report passes only if:
- every factual claim has a `[n]` citation,
- every cited `[n]` exists in sources.md and its notes actually support the
  claim it is attached to (numbers and dates match),
- the `## Sources` list matches sources.md's URLs.

End your reply with exactly one line:
`ACTION: pass` if the report checks out, or
`ACTION: fail` after listing each unsupported or miscited claim (this list
is fed back to the writer). No ACTION line counts as fail.
