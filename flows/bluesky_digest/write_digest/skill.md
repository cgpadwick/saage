---
name: write_digest
description: "Write digest.md from notes.md, ranked by relevance to these interests: {{ interests }}."
tools: [read_file, write_file]
---
SKILL_ID: write_digest

Read `notes.md` and write `digest.md`: a short markdown digest of the posts,
ordered most-to-least relevant to the stated interests. For every item quote
or tightly paraphrase the post, name the author, link its URI — all traceable
verbatim to notes.md — plus a sentence on why it matters. Skip items with no
relevance rather than padding. If feedback from a previous attempt is
provided, fix exactly what it names.
