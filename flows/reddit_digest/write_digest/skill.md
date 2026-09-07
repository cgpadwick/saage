---
name: write_digest
description: "Write digest.md from notes.md, ranked by relevance to these interests: {{ interests }}."
tools: [read_file, write_file]
---
SKILL_ID: write_digest

Read `notes.md` and write `digest.md`: a short markdown digest of the posts,
ordered most-to-least relevant to the stated interests. For every item give
the post's exact title as a link to its URL — both copied verbatim from
notes.md — plus one or two sentences on why it matters. Skip items with no
relevance rather than padding. If feedback from a previous attempt is
provided, fix exactly what it names.
