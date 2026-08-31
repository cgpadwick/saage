---
name: research
description: "Research the question: {{ question }} — run the planned queries, collect at most {{ max_sources }} sources."
tools: [tavily__tavily-search, tavily__tavily-extract, read_file, write_file]
---
SKILL_ID: research

Read `queries.md` and run each query with `tavily__tavily-search`. Pick the
most credible, relevant results (at most the requested number of sources
total) and pull fuller text for the best ones with `tavily__tavily-extract`.

Write `sources.md`: one numbered section per source with its exact URL and
title, followed by bullet-point notes of the FACTS it supports (figures,
dates, named claims — copied faithfully, not embellished). The report writer
may only use what is in this file, so capture everything worth citing. A tool
reply starting with `ERROR:` means that query failed — move on to the next.
