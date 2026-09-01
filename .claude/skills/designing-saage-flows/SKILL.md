---
name: designing-saage-flows
description: Use when the user wants help figuring out WHAT flow to build — "help me design a flow", "walk me through building a workflow", "I want to automate X but don't know how to structure it", or any vague/underspecified flow request. Interviews the user, then hands the agreed design to building-saage-flows to implement.
---

# Designing saage flows (guided interview)

**Any coding agent can use this file** — plain markdown, no Claude-specific
steps. When the user already knows exactly what they want, skip this and use
`building-saage-flows` directly; this skill is for turning a fuzzy goal into a
buildable design by asking questions.

Interview style: **one question at a time**, each with a suggested default so
the user can just say "yes". Skip any question the user's request already
answers. Stop interviewing the moment the design is unambiguous — usually 3–5
questions; never run the full list when the answers are inferable.

## The interview

1. **Goal + finish line.** "What should one run of this flow produce, and how
   would you check it worked?" (a file? a passing test suite? a metric ≥ X?)
   The answer usually IS the check/verify step — deterministic if possible.
2. **Shape.** Match their description to a primitive and confirm it in their
   words, e.g. "so: draft → critique → redraft until the critic accepts, at
   most N rounds — that's a retry loop, sound right?"
   - do → judge → redo with feedback → `retry_loop`
   - submit → wait → poll until done → `polling_loop`
   - iterate until metric/limit → `counting_loop` + `exit_when`
   - straight line of steps → plain `agent`/`command` steps
3. **What's deterministic?** For each step ask: could a shell command do this?
   Tests, builds, data prep, scoring, file shuffling → `command` steps. Only
   judgment/generation belongs in `agent` steps. This is the question users
   most need asked — they default to "the LLM does everything".
4. **Knobs.** "What would you want to tweak between runs without editing the
   flow?" (iterations, target score, topic, dataset path…) → `shared:` keys
   with sane defaults; loop bound templated via `max_iterations: "{{ n |
   default(5) }}"` where applicable.
5. **Bounds + safety.** Confirm every loop's cap (`max_iterations` /
   `max_wait_seconds`) and, for flows that touch files: which directory is the
   workspace? Anything the flow must never touch?
6. **Provider.** Default: no `provider:` block (runs use their `saage setup`
   defaults). Pin one only if they name a model this flow needs.

## Confirm, then build

Before writing any file, play back a compact design summary:

```
flow: <name>
knobs: topic="...", rounds=3
steps:
  1. draft      agent    writes draft.md
  2. review     retry_loop(action=revise, check=critic, max 3)
  3. score      command  python score.py → set: {score: "([0-9.]+)"}
done when: score captured; review passed or 3 rounds spent
```

One "yes" from the user, then switch to **`building-saage-flows`** and follow
its loop exactly: author, hydrate-check, offline integration test, green
pytest — only then offer a live `saage run`. Report back with the run command
and which knobs they can `--set`.

## Interview traps

- A user asking for "an agent that keeps trying until it works" needs a
  bounded loop — always surface the cap you chose and why.
- "Poll every N seconds" without a total cap hangs forever: `polling_loop`
  requires `max_wait_seconds`; ask for the worst-case wait, double it.
- If the finish line is subjective ("until it's good"), make the checker an
  agent skill with an explicit `ACTION: pass` / `ACTION: fail` contract and a
  `retry_loop` — never an unbounded "loop until the model is happy".
- If no step needs judgment at all, say so: they want a shell script or CI
  job, not a flow. Building it anyway helps nobody.
