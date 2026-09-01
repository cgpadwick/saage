---
name: report
description: "Write the triage report as self-contained HTML: executive summary table first, then root cause, repro status, suggested fix."
tools: [read_file, write_file]
---
SKILL_ID: report

Your ONLY deliverable is `triage_report.html`. Verification already happened
upstream (the repro loop ran the repro; its verdict is in the record) — your
job is synthesis, not further investigation. Read `triage_notes.md`,
`triage_issue.json`, `repro_saage.py`, and at most the one or two source
files the notes point at. Then write `triage_report.html`: a single
self-contained HTML file (inline `<style>`, no external assets; system font,
readable line length, bordered tables, monospace `<pre>` blocks for code).

**The document MUST open with an executive summary table** — a reader gets
the whole picture in 30 seconds without scrolling:

| Field | Content |
|---|---|
| Issue | number + title, one line |
| The bug | one plain-English sentence — symptom, when it bites |
| Reproduced | ✅ Verified (repro runs and fails for the issue's reason) or ❌ Unreproduced (loop exhausted; say what blocked it) |
| Root cause | one sentence + the primary `path:line` |
| Suggested fix | one line describing the change (details below) |
| Effort | T-shirt size **XS / S / M / L / XL** + a clause of justification |
| Confidence | High / Medium / Low — how sure the analysis is |

Effort sizing guide: XS = one-line/one-file mechanical change; S = small
localized change, existing tests mostly cover it; M = multi-spot change in
one subsystem, needs new tests; L = crosses subsystems or risks behavior
changes for other callers; XL = design work / API change territory.

After the table, the full sections:
1. **Issue** — restatement (number + title, quoted expected vs actual).
2. **Root cause** — the mechanism, `path:line` references, offending code
   quoted (from the notes and your reads).
3. **Reproduction** — verified or explicitly unreproduced, with the repro's
   observed output quoted.
4. **Suggested fix** — a unified diff in a `<pre>` block, marked as
   UNVALIDATED — proposed from analysis, not executed. Do NOT apply it.
5. **Risks / notes** — affected callers, edge cases, anything unverified.

Be explicit about verified vs hypothesized throughout. Escape `<`, `>`, `&`
inside code blocks. You write only `triage_report.html` — never project
files. If feedback from a previous attempt is provided, your first action is
`write_file` of the complete report; only refine after the file exists.
