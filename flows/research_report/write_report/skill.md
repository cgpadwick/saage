---
name: write_report
description: "Write report.md answering: {{ question }} — every claim cited to sources.md."
tools: [read_file, write_file]
---
SKILL_ID: write_report

Read `sources.md` and write `report.md`: a concise markdown report that
answers the question. Every factual claim carries a citation like `[2]`
pointing at the numbered source in sources.md that supports it, and the
report ends with a `## Sources` list mapping each number to its URL. Nothing
uncited: if sources.md doesn't support a statement, leave the statement out.
If feedback from a previous attempt is provided, fix exactly what it names.
