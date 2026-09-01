---
name: verify_repro
description: "Run the repro and confirm it fails for the issue's stated reason."
tools: [read_file, run_command]
---
SKILL_ID: verify_repro

Run `{{ python }} -P repro_saage.py` yourself with `run_command` (`-P` stops
a source checkout at the workspace root from shadowing the installed
package; drop it only if this python predates 3.11). Then read
`triage_issue.json` and `repro_saage.py` and judge:

- the script exited non-zero, AND
- its failure output matches the SYMPTOM the issue reports (same error type /
  wrong value in the same way) — not an unrelated crash (import error, typo,
  missing fixture), AND
- the script asserts the expected behavior (it would pass once the bug is
  fixed), rather than asserting the bug itself

End your reply with exactly one line:
`ACTION: pass` if all three hold, or
`ACTION: fail` after stating precisely what is wrong (this feeds back to the
repro writer). No ACTION line counts as fail.
