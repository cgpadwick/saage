---
name: verify_repro
description: "Judge the engine-recorded repro run: exit code {{ repro_exit }} (non-zero = script failed). Confirm it failed for the issue's stated reason."
tools: [read_file]
---
SKILL_ID: verify_repro

The engine has ALREADY executed `repro_saage.py` and recorded the result —
the exit code is in your task above, and the full output is in
`triage_repro_out.txt`. You judge that evidence; you do not run anything.

Read `triage_repro_out.txt`, `triage_issue.json`, and `repro_saage.py`, and
judge:

- the recorded exit code is NON-ZERO (if it is 0, the bug did not
  reproduce — that is an automatic fail, whatever the output says), AND
- the captured output matches the SYMPTOM the issue reports (same error
  type / wrong value in the same way) — not an unrelated crash (import
  error, typo, missing fixture), AND
- the script asserts the expected behavior (it would pass once the bug is
  fixed), rather than asserting the bug itself.

End your reply with exactly one line:
`ACTION: pass` if all three hold, or
`ACTION: fail` after stating precisely what is wrong (this feeds back to the
repro writer). No ACTION line counts as fail.
