---
name: locate
description: "Read the fetched issue and locate the code responsible."
tools: [read_file, run_command, write_file]
---
SKILL_ID: locate

You are triaging a bug in the repository you are running inside (a working
checkout — imports and tests already work). Read `triage_issue.json`. If it
contains an error instead of an issue, write `triage_notes.md` saying exactly
that and stop.

Otherwise find where the reported behavior lives: grep for the symbols,
messages, and API names the issue mentions (`run_command` with grep/find),
and read the relevant files. Follow the call path until you can point at
specific code.

Write `triage_notes.md`:
- the issue in one paragraph (expected vs actual, any repro snippet quoted)
- the suspect location(s) as `path:line` with the relevant code quoted
- your hypothesis of the mechanism, clearly labeled hypothesis vs verified

Investigate only — do not modify any project file. The only file you write
is `triage_notes.md`.
