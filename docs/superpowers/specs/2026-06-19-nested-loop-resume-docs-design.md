# Nested-Loop Resume — Documentation Design

**Date:** 2026-06-19
**Status:** Approved (design); implementation pending
**Scope:** Documentation only — no code changes

## Problem

The checkpoint/resume feature ([[2026-06-19-checkpoint-resume-design]]) resumes a
killed run at the **outermost** top-level loop's iteration. When a loop is nested
inside another loop, a crash mid-inner re-enters the *outer* loop and redoes the
entire in-progress outer iteration — re-running the inner loop from scratch.

Nothing stops an author from writing nested loops, and the current docs mention
the limitation only briefly ("a loop nested inside another loop is not
independently resumable"), which is vague and easy to miss. An author could
reasonably expect inner-loop-granularity resume and be surprised when inner work
is redone.

## Finding (empirical)

A repro — `counting_loop` (max 3) nesting a `counting_loop` (max 2), one
command per inner body — crashed mid-inner during outer-iteration 3, then resumed:

| point | inner-body runs | resume_step | status | `_iter` |
|---|---|---|---|---|
| after crash | 4 | 0 (outer) | running | `{outer:2, inner:2}` |
| after resume | 6 | 0 | completed | `{outer:3}` |

`6` equals the clean-run total (3×2): no duplication, no corruption, terminates
correctly. The behavior is **correct but coarse** — resume re-enters the outer
loop at iteration 3 and redoes that iteration with the inner loop from scratch.
This is corroborated by the real `greenfield_ml` run, which nests `retry_loop`s
inside the `hillclimb` `counting_loop` and resumed successfully.

## Decision

The behavior is correct, so do **not** change code. The fix is to make the
outer-granularity limitation **prominent and actionable** in the docs, framed as a
known cost (keep inner loops cheap / prefer one loop level for resumability), not
as a bug. Full inner-granularity resume (a multi-level resume position) was
deliberately deferred in v1 and remains out of scope; no regression test is added
(per the docs-only decision).

## Changes

### 1. `README.md` — "Resumable runs" section

The section states "Resume granularity is one iteration of the outermost loop" but
never spells out nesting. Add one sentence after the existing granularity
sentence:

> A loop nested inside another loop isn't resumed independently — a crash redoes
> the entire in-progress **outer** iteration, re-running the inner loop from
> scratch. It stays correct, but keep inner loops cheap (or prefer a single loop
> level) if resumability matters.

### 2. `AGENTS.md` — two spots

- **Resume bullet** (under "Conventions & gotchas"): replace the vague clause
  "a loop nested inside another loop is not independently resumable" with the
  concrete consequence — on resume the in-progress outer iteration's inner-loop
  work is **redone whole**; keep inner loops cheap or avoid nesting where resume
  cost matters.
- **"Loops nest:" line** (in the step-types reference, where authors first learn
  nesting is allowed): add a short forward-reference noting that resume re-enters
  at the outermost loop, so nested inner-loop progress is redone — so the caveat
  is seen at authoring time, not only in the resume docs.

### 3. `CLAUDE.md` — resumability invariant

Append half a sentence to the resumability invariant bullet: resume re-enters at
the *outermost* top-level step, so a nested inner loop restarts (engine-facing
accuracy).

## Out of scope

- Code changes of any kind (the behavior is correct).
- A guardrail that warns/refuses on nested loops (considered; not chosen).
- Full inner-granularity resume.
- A regression test locking the behavior (considered; not chosen — docs only).

## Testing

None (documentation only). Verification is a proofread of the rendered Markdown
for well-formed fences/links and that the wording matches the finding above.
