# Batched parallel hill-climb: remote fixes first, then the batch primitive

**Date:** 2026-06-11 · **Status:** draft for discussion
**Goal:** fan out *proposals* within a single flow run — propose K diverse
experiments, run them in parallel, collect all results, repropose — and fix
the remote-layer holes that block running K jobs at once.
**Revises:** [kaggle_solver_plan.md](kaggle_solver_plan.md) §3 P3 ("parallel
candidates") and the claim that "P3 parallelism intentionally needs no engine
change"; supersedes the M3 `bench.py`-as-flow-script idea.

## 1. The model

Budget of 20 experiments, capacity of 5 parallel jobs:

    round:  propose 5 DIVERSE experiments (informed by everything so far)
            → implement + short-train all 5 in parallel (5 boxes / 5 slots)
            → collect all 5 results into the research log / ledger
            → keep the best improvement (or none)
            → repropose
    repeat until 20 experiments are spent (4 rounds), then final-train best.

Batch-synchronous parallel tree search (the ML-Master / MLE-STAR shape). Key
property: the proposer sees **all** prior results, including failures, so each
round's K proposals are informed and deliberately diversified — not K blind
trajectories that never talk to each other.

Sequencing decision: **fix the remote layer first.** The batch primitive is
only buildable on top of a remote layer that can actually run and manage N
jobs at once.

## 2. The holes in remote, with receipts

- **H1 — one run per box is a single guard, not an architecture.**
  `saage/remote/target.py` `preflight()` raises if *any* `saage-*` tmux
  session exists on the box ("one run per box; kill it first"). Everything
  node-side is already per-run (`~/.saage_runs/<run_id>/` holds its own venv,
  workspace, secrets, artifacts, status, session — `scripts.py`: "nothing
  shared between runs").
  **Fix:** per-target capacity (`max_runs`, default 1 to keep current
  behavior; settable at `add-target`/`spawn` time). Preflight checks
  session-count < capacity. GPU contention is the user's call via the
  capacity number (CPU/tabular jobs can pack several per box).

- **H2 — dispatch is synchronous and serial.** `handoff()` blocks through
  bootstrap (minutes: rsync engine, uv venv, clone, ws-setup). Fanning out
  5 jobs = 5× that, serially, from the laptop.
  **Fix:** a library-level `dispatch_many(jobs) -> [RunState]` that runs
  handoffs concurrently (threads — handoff is I/O-bound). Later
  optimization, not v1: warm run-dir reuse so repeat jobs on the same box
  skip venv/clone — matters a lot for batched hill-climb, where jobs are
  short and repeated.

- **H3 — no programmatic multi-run API.** status/fetch/kill are CLI print
  functions (`observe.py`); polling N runs means scraping output or reaching
  into private helpers (`_status_from_bucket`).
  **Fix:** promote a small public API in `saage.remote`:
  `poll(run_id) -> phase` (mirror-first, ssh fallback),
  `fetch(run_id, dest) -> files`, `kill(run_id)`. The CLI refactors onto it.
  This is the same scheduler core a competition sweep needs — built once,
  here.

- **H4 — cosmetics that assume exclusivity.** Cost-so-far (`observe.py`)
  bills the box's full hourly rate to every concurrent run on it; `ps` notes
  assume session↔run is 1:1-ish. Minor; fix alongside H1.

- **H5 — (kaggle, already known)** `flows/kaggle_solver/flow.yaml` declares
  no `artifacts:` — `submission.csv` never syncs back from a node. One-line
  fix.

- **H6 — every run re-does the expensive setup.** The node convention is
  deliberately "everything per-run, nothing shared between runs"
  (`scripts.py`): each handoff rsyncs the engine, builds a venv, clones the
  workspace, and runs `--ws-setup` (dataset pull, ML venv — the multi-GB,
  multi-minute part) from scratch. Fine for one long run; brutal for K
  short experiment jobs per round, and K concurrent bootstraps on one box
  would pull the same dataset K times, racing each other.
  **Fix: one provision per node, content-keyed cache, per-run state stays
  isolated.**

  ```
  ~/.saage_cache/                      # shared across runs on the node
    datasets/<key>/                    # e.g. mlebench/spooky-author — immutable
    venvs/<hash-of-requirements>/      # the torch-class ML venv, built once
    repos/<repo>.git                   # bare mirror; clones become seconds
    engine/<sha>/                      # optional: engine source + venv
  ```

  - Population is **idempotent and locked**: the provision script takes an
    `flock` per cache key, checks a `.ready` stamp, and either populates or
    waits — so 5 simultaneous bootstraps = 1 dataset pull + 4 cheap waits.
  - Keys are content-derived (dataset id, requirements hash, engine sha),
    so there is no invalidation problem — only eventual GC.
  - The per-run workspace then *links into* the cache: `ws/data →
    cache/datasets/<key>`, `ws/.venv → cache/venvs/<hash>` (which means the
    existing flow.yaml `venv:` auto-activation works unchanged), and the
    clone uses the bare mirror (`git clone --reference`).
  - Per-job residual cost: engine rsync + uv venv (seconds — uv's wheel
    cache hardlinks) + clone from local mirror (seconds) + symlinks.
    Well under a minute per experiment job.
  - Two ways to express "provision once": (a) implicit — the first job's
    locked ws-setup populates the cache, the rest wait; (b) explicit —
    `dispatch_many` runs a provision step once per node (per setup-hash
    stamp) before dispatching, so job dispatch is uniformly fast and
    provisioning failures are distinguishable from job failures. Lean: (b),
    with (a)'s locking still in place as the correctness backstop.

## 3. Build order

**Phase 1 — remote fixes** (no engine/flow changes, independently testable):

1. H1 capacity + H4 cost attribution (+ tests in `tests/remote/`).
2. H3 public poll/fetch/kill API (refactor `observe.py` CLI onto it).
3. H6 node cache + locked, idempotent provision (cache layout, flock
   helper, provision-stamp; flow setup scripts converted to use it).
4. H2 `dispatch_many` with concurrent handoffs + provision-once-per-node,
   including the reaper (§4): per-job deadlines, mirror-based node
   liveness, lost-job requeue.

*Acceptance:* from one laptop, launch 5 concurrent runs across 2 boxes
(capacity 2+3), poll them as a set, fetch all artifacts, no collisions —
and the dataset/ML-venv setup ran **once per box**, not once per run.
*Failure drill:* kill one box mid-run — its jobs requeue and complete on
the surviving box, the replacement (if spawned) provisions itself cold.

**Phase 2 — the batch primitive** (design settles after phase 1; sketch):

The hill-climb loop becomes: per round,

- **propose_batch:** agent produces K diverse proposals (sees full ledger).
- **dispatch:** K experiment jobs = a small per-experiment flow
  (implement → smoke → short-train → report score), each seeded with
  `{proposal, base commit/branch}` via `--set`, run through the phase-1
  dispatch layer (remote slots) or local subprocesses.
- **barrier:** collect K scores/diffs/logs into the parent ledger.
- **select:** keep best improvement — generalized keep_or_revert (best-of-K
  vs current best), merge its branch into the run branch.
- loop until total experiments ≥ budget.

Open design questions for phase 2 (deliberately not settled now):

- Where does the coordinator run? (laptop vs a coordinator box)
- One agent call proposing K vs K calls with diversity pressure?
- Engine shape: a new `batch` step type, vs `counting_loop` + a dispatch
  command step the engine doesn't even know is parallel?
- Worker bootstrap cost per short job (argues for H2's warm reuse).

## 4. Failure handling: watchdogs, reapers, and dead boxes

What already exists (keep all of it): a per-run **node-side watchdog**
(`start.sh`: wall-clock cap → kills the run, writes `status timeout`), a
**heartbeat** (`status.json` rewritten every sync interval, mirrored to R2 —
so the last heartbeat *survives box death*), fast-fail reporting (a crashed
run writes `status failed` + rc), and orphan detection (`ps`/reconcile).
What's missing is the **coordinator-side reaper**: today a human polls.

### Failure taxonomy → response

| Failure | Detected by | Response |
|---|---|---|
| Job crashes fast | node `status failed` + rc | record as failed experiment (score = nan); round proceeds — already works |
| Job hangs | per-job timeout: node watchdog (tight for short jobs, hours not days) + coordinator deadline as backstop | remote kill if reachable; counts as failed experiment |
| Box dies (spot reclaim, owner kill, panic) | heartbeat in R2 goes stale (> ~3 sync intervals) AND ssh unreachable | node → dead; its in-flight jobs → `lost`, requeued elsewhere (bounded retries) |
| Box alive, ssh flaky | R2 heartbeat still fresh | not dead — keep waiting; never kill on reachability alone |
| Provision fails | explicit provision step rc | quarantine node, dispatch nothing to it |

### The reaper (new, lives in the phase-1 dispatch layer)

A supervision loop the coordinator runs while jobs are in flight:

- **per-job deadline** → kill → resolve as `timeout`.
- **per-node liveness** = freshest heartbeat across the node's runs, read
  from the R2 mirror (works when the box is gone), ssh ping as tiebreak.
  Two signals on purpose: stale-mirror-but-ssh-fine ≠ dead.
- **dead node** → mark jobs `lost` → requeue (attempts ≤ 2) → optionally
  spawn a replacement (`--respawn`, a cost decision, only for boxes we
  spawned).
- Job state machine: `queued → dispatching → running →
  {done | failed | timeout | lost}`; `lost/timeout → requeued` while
  attempts remain. Node: `provisioning → ready → suspect → dead/quarantined`.
- For short experiment jobs, drop `sync_interval` (~60s) so death detection
  is timely; heartbeat puts to R2 are negligible.

### Why "setup again on a new box" is not the hard part

It falls out of H6's design: provisioning is an **idempotent, content-keyed
function of the node** (stamps in `~/.saage_cache/`). A replacement box
simply has no stamps, so `dispatch_many` provisions it exactly like any
first-time node before sending jobs — no special recovery path, no state to
reconstruct. The full data-pull cost on a fresh box is unavoidable (the
bytes have to get there), but it's automatic, bounded, and identical to the
cold-start path that's tested anyway.

### What requeue safety demands of jobs (phase-2 contract)

- An experiment job is a **pure function of its inputs** — `{base sha,
  proposal, seed}` all passed via `--set`; re-dispatch is replay, not
  duplication.
- Each attempt gets a fresh `run_id` → no artifact collision; the parent
  ledger is written by the **coordinator only at collect time**, so a lost
  job never half-writes shared state, and if a zombie box completes a
  requeued job twice, the first resolution wins and the second is ignored.
- **Round/barrier policy:** a round resolves when all K jobs reach a final
  state — per-job timeouts bound the round, so a straggler can't hold it
  hostage forever. (Optional later: "proceed at ≥M of K, kill the rest" —
  deliberately not v1.)

## 5. What this supersedes from earlier drafts

- **`bench.py` as a kaggle-local driver:** dead. The dispatch/poll/fetch
  core moves into `saage.remote` (phase 1); a competition sweep (M3) and
  the batched hill-climb both become thin users of it.
- **`fan_out` around the whole flow body** (K independent trajectories
  joined at the end): dead as proposed; the batch loop above replaces it —
  trajectories must share a ledger between rounds, not meet once at a join.
