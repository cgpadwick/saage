# Nested-Loop Resume Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the "nested loops resume at outermost-loop granularity (inner work is redone, but correct)" limitation prominent and actionable in the docs.

**Architecture:** Documentation-only. Three Markdown edits — README (user-facing resume docs), AGENTS.md (flow-author docs, two spots), CLAUDE.md (engine-facing invariant). No code, no tests.

**Tech Stack:** Markdown.

**Spec:** `docs/superpowers/specs/2026-06-19-nested-loop-resume-docs-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `README.md` | User-facing "Resumable runs" section — add the nested-loop consequence | Modify |
| `AGENTS.md` | Flow-author docs — strengthen the resume bullet + forward-reference at "Loops nest:" | Modify |
| `CLAUDE.md` | Engine-facing resumability invariant — note outermost re-entry | Modify |

All three are independent single-string edits. There is **no test step** (docs-only, per the spec's "Testing: None"); verification is a Markdown proofread.

---

## Task 1: README — spell out nested-loop resume

**Files:**
- Modify: `README.md` (the "## Resumable runs" section)

- [ ] **Step 1: Make the edit**

Find this exact paragraph in `README.md` (end of the "Resumable runs" section):

```
`saage run` always starts a fresh run. Resume granularity is one iteration of the
outermost loop: a 12-iteration hill-climb killed during iteration 10 resumes at
iteration 10, keeping 1–9. The killed iteration is redone from its start, so a
flow's loop body should be safe to re-run (e.g. clean a checkpoint dir, then
train) — the example ML flows already follow this pattern.
```

Replace it with (adds one sentence about nesting at the end):

```
`saage run` always starts a fresh run. Resume granularity is one iteration of the
outermost loop: a 12-iteration hill-climb killed during iteration 10 resumes at
iteration 10, keeping 1–9. The killed iteration is redone from its start, so a
flow's loop body should be safe to re-run (e.g. clean a checkpoint dir, then
train) — the example ML flows already follow this pattern.

A loop nested inside another loop isn't resumed independently: a crash redoes the
entire in-progress **outer** iteration, re-running the inner loop from scratch.
The result stays correct, but keep inner loops cheap (or prefer a single loop
level) if resumability matters.
```

- [ ] **Step 2: Verify**

Run: `sed -n '/## Resumable runs/,/^## /p' README.md`
Expected: the section now ends with the new "A loop nested inside another loop…" paragraph, followed by the next `##` heading. Code fences in the section are balanced (the ```bash block opens and closes).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): note nested loops resume at outer-iteration granularity"
```

---

## Task 2: AGENTS.md — strengthen the resume bullet and add a forward-reference

**Files:**
- Modify: `AGENTS.md` (the "Resumable runs / restart-safe iterations" bullet, and the "Loops nest:" line)

- [ ] **Step 1: Strengthen the resume bullet**

Find this exact bullet in `AGENTS.md` (under "Conventions & gotchas"):

```
- **Resumable runs / restart-safe iterations.** `saage run` checkpoints after
  every step and loop iteration; `saage resume` restarts at the top-level step
  that was in progress. A killed loop iteration is redone *whole* from the body's
  first step, so write loop bodies to tolerate re-running the current iteration
  (e.g. clean the experiment dir at the top of the body before training, as the
  hill-climb flows do). Completed iterations are never redone. Resume granularity
  is the *outermost* loop's iteration; a loop nested inside another loop is not
  independently resumable.
```

Replace it with (concrete consequence + guidance in place of the vague last clause):

```
- **Resumable runs / restart-safe iterations.** `saage run` checkpoints after
  every step and loop iteration; `saage resume` restarts at the top-level step
  that was in progress. A killed loop iteration is redone *whole* from the body's
  first step, so write loop bodies to tolerate re-running the current iteration
  (e.g. clean the experiment dir at the top of the body before training, as the
  hill-climb flows do). Completed iterations are never redone. Resume granularity
  is the *outermost* loop's iteration: a loop nested inside another loop is **not**
  resumed independently — a crash redoes the whole in-progress outer iteration and
  re-runs the inner loop from scratch. It stays correct, but keep inner loops
  cheap (or avoid nesting) where resume cost matters.
```

- [ ] **Step 2: Add a forward-reference at "Loops nest:"**

Find this exact sentence in `AGENTS.md` (in the step-types reference):

```
Loops nest: an `action`/`check`/`body` entry can itself be a loop. A nested loop's
counter is reset each time the outer loop re-enters it.
```

Replace it with (adds the resume caveat where nesting is introduced):

```
Loops nest: an `action`/`check`/`body` entry can itself be a loop. A nested loop's
counter is reset each time the outer loop re-enters it. (Resume caveat: `saage
resume` re-enters only at the *outermost* loop, so a crash redoes the whole
in-progress outer iteration and reruns nested inner loops from scratch — see
"Resumable runs / restart-safe iterations" under Conventions & gotchas.)
```

- [ ] **Step 3: Verify**

Run: `grep -n "resumed independently\|Resume caveat\|outermost" AGENTS.md`
Expected: the strengthened bullet (with "not** resumed independently") and the new "Resume caveat:" parenthetical at the "Loops nest:" line both appear.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): make nested-loop resume caveat concrete + add it where nesting is introduced"
```

---

## Task 3: CLAUDE.md — note outermost re-entry in the resumability invariant

**Files:**
- Modify: `CLAUDE.md` (the "Resumability rides on the shared store" invariant bullet)

- [ ] **Step 1: Make the edit**

Find this exact bullet in `CLAUDE.md` (in the Architecture key-invariants list):

```
- *Resumability rides on the shared store.* `saage/checkpoint.py` JSON-snapshots
  `shared` after each node (via `Subflow._orch`), tagged with `resume_step` (the
  *next* node's `_step_index`, set in `hydrate.py`). `saage resume` restores
  `shared` and sets the top-level `start_node` to `steps[resume_step]`. Keep
  everything written into `shared` JSON-serializable, or checkpoints degrade to
  `str()` coercion.
```

Replace it with (adds one sentence on outermost re-entry):

```
- *Resumability rides on the shared store.* `saage/checkpoint.py` JSON-snapshots
  `shared` after each node (via `Subflow._orch`), tagged with `resume_step` (the
  *next* node's `_step_index`, set in `hydrate.py`). `saage resume` restores
  `shared` and sets the top-level `start_node` to `steps[resume_step]`. Because
  `resume_step` is a *top-level* step index, resume re-enters at the outermost
  loop — a nested inner loop restarts (its `_iter` is not preserved). Keep
  everything written into `shared` JSON-serializable, or checkpoints degrade to
  `str()` coercion.
```

- [ ] **Step 2: Verify**

Run: `grep -n "outermost loop — a nested inner loop restarts" CLAUDE.md`
Expected: one match (the new sentence is present in the invariant bullet).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): note resume re-enters at the outermost loop"
```

---

## Self-Review

**Spec coverage:**
- README nested-loop sentence → Task 1.
- AGENTS.md resume bullet strengthened → Task 2 Step 1.
- AGENTS.md forward-reference at "Loops nest:" → Task 2 Step 2.
- CLAUDE.md outermost re-entry note → Task 3.
- Out of scope (no code/guardrail/regression test) → honored: this plan has zero code/test steps.

**Placeholder scan:** No TBD/TODO; every step has the exact old and new Markdown text and a concrete verify command.

**Consistency:** Wording ("redoes the whole in-progress outer iteration, re-runs the inner loop from scratch", "stays correct", "keep inner loops cheap") is consistent across README, AGENTS.md, and the spec's finding. The framing is "correct but coarse," never "broken," matching the spec.
