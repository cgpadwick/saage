# User-Input (console) Tool — Design

**Status:** implemented autonomously (2026-06-24, user stepped out). Closes #31.

## Goal

A harness tool that **pauses the workflow, prompts the human on the console,
reads what they type (one line + Enter), and resumes** — for flows that need a
confirmation, plan approval, or a clarification mid-run.

## Decisions

- **`ask_user(prompt)`** — a `Tool` in `default_tools()` (via `user_tools()`),
  opt-in per skill through the `tools:` allow-list (same gating as the other
  tools). It blocks the agent loop on `input()` — that's the point: the run waits
  for the human, then continues with the typed line as the tool result.
- **Single line.** Reads one line (`input()`), trailing whitespace stripped. A
  multi-line variant is YAGNI for now.
- **Headless safety (the key call).** Most `saage run`s are backgrounded /
  piped / remote / CI, where stdin is not a TTY and `input()` would hit EOF or
  hang. So `ask_user` checks `sys.stdin.isatty()` first: if it's **not** an
  interactive terminal it returns an `ERROR:` string (the agent reacts, the run
  continues) instead of blocking forever. An `EOFError` mid-read is likewise
  returned as a graceful `ERROR:`. This keeps the existing background-run workflow
  (nohup, `saage remote`) safe by default.
- Helpers (`input`, `isatty`) are injectable so the behavior is fully unit-tested
  without a real terminal.

## Tests

`tests/test_user_input.py` — returns the typed line (stripped); non-TTY returns an
ERROR **without** calling `input()` (never blocks); EOF is graceful; `ask_user` is
in `default_tools`. `tests/test_tools.py` count updated. Full suite green
(370 passed, 7 skipped).

## Out of scope / future

- Multi-line / structured input (e.g. read until a sentinel).
- A non-console channel (e.g. `saage remote` could forward the prompt to the
  laptop) — for now remote/headless just returns the ERROR.
