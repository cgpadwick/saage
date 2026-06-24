# User-Input (console) Tool — Design

**Status:** implemented autonomously (2026-06-24, user stepped out). Closes #31.

## Goal

A harness tool that **pauses the workflow, prompts the human on the console,
reads what they type (one line + Enter), and resumes** — for flows that need a
confirmation, plan approval, or a clarification mid-run.

## Decisions

- **`ask_user(prompt)`** blocks the agent loop on `input()` — that's the point:
  the run waits for the human, then continues with the typed line as the tool
  result.
- **Opt-in (NOT a default tool).** Because it blocks, `ask_user` is deliberately
  kept out of `default_tools()`: a skill gets it only by naming `ask_user` in its
  `tools:` allow-list (`tools.opt_in_tools` + `AgentNode` grant it on request).
  So a no-allow-list agent in an autonomous flow (greenfield/lewm/kaggle) can
  **never** call it and stall the run — only a skill that explicitly wants a
  human in the loop sees it. (Code-review finding: a blocking tool in the default
  set could hang a foreground autonomous run.)
- **Single line.** Reads one line (`input()`), trailing whitespace stripped.
- **Never blocks or aborts.** Returns a graceful `ERROR:` string (the agent
  reacts, the run continues) in every non-interactive / cancelled case:
  `sys.stdin.isatty()` is false (backgrounded / piped / CI), `sys.stdin is None`
  (embedded / detached), `EOFError`, or `KeyboardInterrupt` (Ctrl+C). Catching
  `KeyboardInterrupt` matters because it is a `BaseException` — uncaught it would
  escape `run_agent`'s `except Exception` and kill the whole run.
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
