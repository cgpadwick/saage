---
name: write_repro
description: "Write a self-contained script that demonstrably reproduces the issue."
tools: [read_file, write_file, run_command]
---
SKILL_ID: write_repro

Read `triage_notes.md` (and `triage_issue.json` if you need the original
wording). Write `repro_saage.py`: a minimal, self-contained script that
demonstrates the reported bug against this checkout.

Contract for the script:
- exits NON-ZERO while the bug is present (assert the expected behavior, or
  let the reported crash propagate), printing what it expected vs what it got
- would exit zero on a fixed codebase — assert the EXPECTED behavior, never
  the buggy one
- no network, no project files modified

Run it with `{{ python }} repro_saage.py` and iterate until its failure
matches the issue's reported symptom. If feedback from a previous attempt is
provided, fix exactly what it names. If the issue genuinely cannot be
reproduced this way (needs external services, hardware, races), say so
explicitly in your final message and leave your best attempt in place.

Do not modify any project file — you write only `repro_saage.py`.
