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

Run it with `PYTHONSAFEPATH=1 {{ python }} repro_saage.py` and iterate until
its failure matches the issue's reported symptom — the engine will afterwards
execute exactly that invocation itself and record the exit code, so make sure
it behaves under it. `PYTHONSAFEPATH` keeps a source checkout at the
workspace root from shadowing the installed package (python 3.11+; a no-op
before that). If the project is NOT installed into the venv (no editable
install), start the script with `sys.path.insert(0, os.path.dirname(
os.path.abspath(__file__)))` so its imports still resolve. If feedback from a previous attempt is
provided, fix exactly what it names. If the issue genuinely cannot be
reproduced this way (needs external services, hardware, races), say so
explicitly in your final message and leave your best attempt in place.

Do not modify any project file — you write only `repro_saage.py`.
