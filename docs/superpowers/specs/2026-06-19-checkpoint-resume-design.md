# Checkpoint & Resume — Design

**Date:** 2026-06-19
**Status:** Approved (design); implementation pending
**Scope:** v1 — local, engine-level, manually-triggered resume

## Problem

A `saage` run can be long-lived (a hill-climb of 12 experiments, each a multi-hour
train; a polling loop waiting on a remote job). Today, if the engine process dies —
laptop battery, `Ctrl-C`, an `ssh` drop on a remote node — the entire run is lost and
must restart from the first step. We want a run to be **resumable**: the engine records
its progress as it goes, and a killed run can be picked up roughly where it left off.

## Goals

- A killed run can be resumed and continues without redoing already-completed work.
- Resume granularity is **one loop iteration of the outermost loop** (and one top-level
  step elsewhere). Example: a 12-iteration hill-climb killed during iteration 10 resumes
  at iteration 10, keeping iterations 1–9.
- Manual, explicit resume via the CLI. No auto-detection magic.
- Zero changes required to existing flows — any flow becomes resumable for free.

## Non-goals (v1)

- **Automatic remote recovery.** The engine running on a remote box becomes resumable
  because it is the *same* engine, but wiring the remote watchdog/tmux to auto-resume a
  crashed node is a later phase. v1 is local + manual; remote auto-restart follows once
  this works.
- **Per-node / mid-iteration resume.** A killed iteration is redone *whole*, not resumed
  partway through. The iteration is the transactional unit.
- **Independent resume of a loop nested inside another loop.** Resume granularity is the
  *outermost* loop's iteration (see Limitations).
- **Mid-LLM-call resume.** A killed `agent` step re-runs from the start of its step; we
  never checkpoint inside an agent's tool-use loop (it is non-deterministic anyway).

## Key decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Checkpoint grain | Per top-level step, and per outermost-loop iteration |
| Re-entry mechanism | Restore `shared`, rebuild flow, set top-level `start_node` to the resume step |
| v1 reach | Local engine-level, manual resume; remote auto-restart later |
| Trigger UX | `saage resume [<id>]` + `saage runs`; `saage run` always starts fresh |
| Stale flow handling | Fingerprint flow.yaml + skills; refuse on mismatch unless `--force` |
| Storage | `~/.saage/runs/<run_id>/checkpoint.json` (unify with remote run registry) |

## Why this is feasible (two verified facts)

1. **`shared` is the single source of truth.** A run is `flow.run(seed)` where `seed`
   *is* the shared store. All cross-step state already lives there: loop counters
   (`_iter`), `results`, captured values, `_feedback`, `_trace`. Everything in it is
   JSON-serializable by construction (captures coerce to str/int/float; results are str
   or dict-of-str). So a checkpoint is just a JSON dump of `shared`.

2. **PocketFlow orchestration walks `successors` from `start_node`.** From
   `pocketflow.Flow._orch`:
   ```python
   def _orch(self, shared, params=None):
       curr = copy.copy(self.start_node)
       while curr:
           curr.set_params(p)
           last_action = curr._run(shared)
           curr = copy.copy(self.get_next_node(curr, last_action))
   ```
   The `>>` chain lives on the node objects. To resume at top-level step *k*, rebuild the
   flow and set the top-level flow's `start_node = steps[k]`. Steps `0..k-1` simply never
   run; their results are already in the restored `shared`. **Resume requires no changes
   to PocketFlow internals — only a different start node.**

   `copy.copy` is shallow, so attributes we attach to node/flow objects (the
   `_step_index` tag, the checkpoint `sink`) survive the per-tick copy.

## Position model: two values, not one

A run's position is stored in two separate places. This is the crux of the design:

| What | Where | Example |
|---|---|---|
| Which top-level step | `resume_step` (checkpoint field) | `7` |
| Where inside a loop | `shared["_iter"][loop_name]` (already exists) | `{"hillclimb": 6}` |

`resume_step` is a **pointer into the top-level `workflow:` list**, not a counter. For
greenfield_ml the list has 10 items, so `resume_step` only ranges 0–9. While the
`hillclimb` loop (item 7) runs all 12 iterations, `resume_step` stays pinned at `7`; the
iteration number lives in `shared["_iter"]["hillclimb"]`. A checkpoint mid-hillclimb is
`resume_step=7` + `_iter={"hillclimb": 9}`.

Restoring `shared` brings back the loop counters for free; `resume_step` only has to say
which top-level box to re-enter.

## Components

### 1. New module: `saage/checkpoint.py`

A single focused unit, modeled on the existing `saage/remote/state.py`.

- **`new_run_id()`** → `YYYYMMDD-HHMMSS-<short>` (the engine may use time/uuid freely).
- **`Checkpoint`** class:
  - holds the run dir (`~/.saage/runs/<run_id>/`);
  - `write(shared, resume_step, status)` — atomic tmp+rename write of `checkpoint.json`;
    serializes with `json.dumps(..., default=str)` defensively and logs a warning if a
    non-serializable value appears (the invariant is "`shared` stays JSON-able");
  - `load()` → the record dict;
  - `mark(status)` — convenience to update only `status` + `updated_at`.
- **Run registry**: `list_runs()` / `find_run(ref)` — resolve by id or unique prefix,
  latest-by-default, mirroring `remote/state.py`'s helpers.
- **`fingerprint(flow_path)`** → `"sha256:<hex>"` over `flow.yaml` + every `skill.md`
  and skill `.py` in the flow directory (sorted, content-hashed).
- **`saage_home()` / `runs_dir()`**: factor the path helpers currently in
  `remote/creds.py` into a neutral location both `checkpoint.py` and `remote/` import,
  so the engine package does not depend on the remote package.

**Checkpoint record (`checkpoint.json`):**
```json
{
  "run_id": "20260619-153012-a1b2",
  "status": "running",
  "flow_path": "/abs/flows/greenfield_ml/flow.yaml",
  "workspace": "/abs/tmp/saage_mnist",
  "venv": ".venv",
  "fingerprint": "sha256:...",
  "provider_overrides": { "type": "openrouter", "model": "..." },
  "config_path": "/abs/engine.yaml",
  "resume_step": 7,
  "shared": { "...": "full shared store" },
  "started_at": "2026-06-19T15:30:12Z",
  "updated_at": "2026-06-19T19:02:44Z"
}
```
`provider_overrides` / `config_path` / `workspace` / `venv` are recorded so `saage
resume` relaunches identically without re-specifying flags.

### 2. Node tagging (`saage/hydrate.py`)

After `build_flow` builds the top-level `steps` list, walk each step's node subtree
(follow `successors` transitively from its start node) and set `node._step_index = k` on
every node **and** on the step's own `Subflow` object. A helper `_tag_step(node, k,
seen)` with a `seen` set handles the loop-back edges in primitive subflows.

This is the only addition to hydrate beyond threading the checkpoint `sink` through
`Context` to every `Subflow` that gets built.

### 3. Checkpoint writing (`saage/primitives.py` — `Subflow`)

`Subflow` gains an optional `sink: Checkpoint | None`, attached at build time to **both**
the top-level flow and every loop subflow. Override `Subflow._orch` with the stock
PocketFlow loop plus a checkpoint write after each `curr._run(shared)`. `resume_step`
records the **next** node's top-level step index (falling back to the current node's index
inside a loop body or at the terminal node), so resuming never re-runs a just-completed
linear step, while a loop body — whose next node shares the loop's `_step_index` — still
resumes the loop in place:

```python
nxt = self.get_next_node(curr, last_action)
if self.sink is not None:
    curr_idx = getattr(curr, "_step_index", None)
    nxt_idx = getattr(nxt, "_step_index", None) if nxt is not None else None
    resume_step = nxt_idx if (nxt_idx is not None and nxt_idx != curr_idx) else curr_idx
    self.sink.write(shared, resume_step, "running")
```

Because loop bodies execute inside their own subflow's `_orch`, this yields **per-
iteration** writes (the body nodes carry the enclosing loop's `_step_index`), while
top-level steps get **per-step** writes. A loop subflow and its parent occasionally write
at the same `resume_step`; that is harmless last-write-wins.

The `sink` attribute is excluded from the serialized `shared` (it lives on the flow
object, not in the store), so it never pollutes `checkpoint.json`.

### 4. Resume re-entry (`saage/hydrate.py` + `saage/cli.py`)

`build_flow` / `run_flow` gain a `resume: Checkpoint | None` parameter. When resuming:

1. **Fingerprint check.** Compare the live `fingerprint(flow_path)` with the recorded
   one. On mismatch, refuse with a message naming the changed files, unless `--force`.
2. **Rebuild** the flow normally using the recorded `flow_path`, `workspace`, `venv`,
   `provider_overrides`, and `config_path`.
3. **Seed `shared`** from the checkpoint's `shared` (the whole store), not a fresh seed.
4. **Set the start node:** `top_level_flow.start_node = steps[resume_step]`.
5. **Loop counter-reset suppression.** `Subflow.prep` currently clears `_iter[name]`
   (etc.) on entry so a nested loop restarts each time its outer loop re-enters it. On
   resume we must *not* clear the resumed loop's counter. If `steps[resume_step]` is a
   loop (`Subflow`), set a one-shot `_skip_reset_once = True` attribute directly on that
   subflow object; `Subflow.prep` skips its reset once and clears the flag. This keeps
   the transient control flag *off* the persisted `shared` store (so it can never leak
   into a checkpoint) and naturally handles nested loops — only the outermost subflow
   skips its reset; inner loops reset as normal. (If `steps[resume_step]` is a plain
   node, no flag is set — there is no reset to suppress.)
6. **Polling clocks.** Drop `_poll_start` and `_poll_count` from the restored `shared`.
   They hold `time.monotonic()` values from the dead process, which are meaningless in a
   new process. A resumed `polling_loop` therefore restarts its wall-clock window — it
   re-polls the job, and never re-submits it.

### 5. CLI surface (`saage/cli.py`)

- **`saage run …`** — unchanged behavior, but now: generate a `run_id`, create the
  `Checkpoint`, thread the sink into `build_flow`, write checkpoints during the run, and
  mark `completed` (clean end) or `failed` (engine exception / failed terminal action) at
  the end. **Always starts fresh.**
- **`saage resume [<run_id>] [--force]`** — resume the latest resumable run, or one by id
  / unique prefix. `--force` overrides a fingerprint mismatch.
- **`saage runs`** — list runs: `run_id`, flow, status, a human position
  (`step 3/9`, or `hillclimb iter 7/12` when the resume step is a loop), and
  last-updated time.

**Status lifecycle:** `running` → `completed` | `failed`. A run left in `running` (the
process died without a final update) is treated as resumable. v1 treats any
non-`completed` run as resumable and shows `updated_at` so the user can judge staleness.
A pid/liveness check to distinguish "crashed" from "still running" is a noted
nice-to-have, not v1.

### 6. Library vs CLI

Checkpointing is **opt-in at the library level**: `run_flow(..., checkpoint=None)`
defaults to today's behavior (no checkpoint files), keeping `saage` pure as a library —
the host app stays in control. The **CLI turns it on by default** so every `saage run` is
resumable.

## Worked example: greenfield_ml hill-climb

Top-level `workflow:` (10 items): `setup, git_init, data, baseline_build, train,
evaluate, baseline_commit, hillclimb, report_narrative, report`. `hillclimb` is item 7
and is the **outermost** loop (its body contains inner `retry_loop`s, but it is itself a
top-level step).

Battery dies during iteration 10's multi-hour train:

- After iteration 9's `keep_or_revert`, the loop's `GateNode` increments
  `_iter["hillclimb"]` to 9 and a checkpoint is written: `resume_step=7`,
  `_iter={"hillclimb": 9}`, `best_score` = best of 1–9.
- Iteration 10 begins (propose → implement → train…) and is interrupted.
- `saage resume`: restores `shared` (so `best_score` and `_iter["hillclimb"]=9` come
  back), rebuilds the flow, sets `start_node = steps[7]`, sets
  `shared["_resume_step"]=7` so the hillclimb loop skips its counter reset. The loop body
  runs iteration 10 from the top; the gate increments to 10 and continues toward
  `max_iterations`/`exit_when`.

**Net: iterations 1–9 kept; iteration 10 redone from its start.** These flows are
naturally restart-safe because each iteration begins by cleaning its checkpoint dir and
training fresh (`clean` → `train`), so redoing iteration 10 wipes any partial output and
starts clean.

## Limitations (documented, by design)

- **Outermost-loop granularity.** A loop nested inside another loop is not independently
  resumable. A crash during inner iteration 2 of outer iteration 3 redoes all of outer
  iteration 3 (inner from scratch). This matches the engine's existing semantics ("a
  nested loop's counter resets each time the outer loop re-enters it") and the per-loop-
  iteration grain chosen for v1. Hill-climb is unaffected because hill-climb *is* the
  outermost loop.
- **A killed iteration is redone whole**, including a long train that was partway done.
  Flows should make each iteration restart-safe (the clean→train pattern). This is a
  guidance note for AGENTS.md.
- **`shared` must stay JSON-serializable.** True today by construction; the checkpoint
  writer guards with `default=str` + a warning so a regression is visible.

## Testing (offline, mirrors the existing suite)

- **`tests/test_checkpoint.py`** (unit, mirrors `tests/remote/test_state.py`):
  atomic write/load; `fingerprint` stability and change-detection; registry
  `list_runs`/`find_run` (latest, by-prefix, ambiguous, missing).
- **Node tagging** (unit): build a real flow (e.g. greenfield_ml) and assert every node's
  `_step_index` is correct, including nodes inside loops and the loop subflow objects.
- **`tests/integration/test_resume.py`** (uses `RoutedProvider`, fully offline): run a
  `counting_loop` flow, simulate a crash by raising inside a node at iteration *k*, catch
  it, then `resume` from the on-disk checkpoint. Assert:
  - a side-effecting `command` step from completed iterations did **not** re-run (e.g. a
    file it appends to has the expected line count);
  - the loop continues from iteration *k* (not 1) and finishes correctly;
  - the final `shared` matches an equivalent uninterrupted run.
- **Reset-suppression** (integration): a resumed loop's gate counter continues from the
  restored value rather than restarting at 0/1.
- **Fingerprint refusal** (unit/integration): editing a `skill.md` between checkpoint and
  resume causes `saage resume` to refuse, and `--force` overrides it.

## Open follow-ups (not v1)

- Remote auto-restart: the remote watchdog relaunches the engine under tmux and calls
  `saage resume` on the node's local checkpoint after a crash.
- pid/heartbeat liveness so `saage runs` can show "running" vs "crashed".
- Checkpoint retention/pruning policy for old completed runs.
</content>
