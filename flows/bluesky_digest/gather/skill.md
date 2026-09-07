---
name: gather
description: "Collect recent posts from these Bluesky accounts: {{ handles }} ({{ posts_per_account }} posts each), and save structured notes."
tools: [bluesky__get_author_feed, bluesky__get_profile, write_file]
---
SKILL_ID: gather

You are collecting raw material for a digest. For EACH handle listed in the
task (comma-separated), call `bluesky__get_author_feed` with that handle and
the requested limit.

Then write `notes.md`: one section per account, one bullet per original post
(skip pure reposts) with its exact text (or first ~200 chars for long posts),
timestamp, and URI/link — copied verbatim from the tool results, never
invented or reworded. Note like/repost counts when present.

A tool reply starting with `ERROR:` means that account failed (bad handle,
rate limit) — note it in notes.md and continue with the rest.
