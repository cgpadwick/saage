# Remote Checkpoint & Resume — Design

**Date:** 2026-06-20
**Status:** Approved (design); implementation pending
**Scope:** Make a remote run's checkpoint (and best model) durable in R2, and add
a manual command to resume a killed/crashed remote run — in place if the node
lives, on a fresh box from R2 if it's gone.

Builds on the engine checkpoint/resume feature
([[2026-06-19-checkpoint-resume-design]]), which already writes a node-side
checkpoint during a remote run but does not mirror or reuse it.

## Problem

A remote run can take days on a GPU box. Today, if the engine crashes (OOM,
transient) or the box dies, the run is lost — and the trained best model with it.
The engine already checkpoints on the node (`~/.saage/runs/<id>/checkpoint.json`),
but it is never mirrored off-box and `start.sh` always `saage run`s fresh, never
`saage resume`. We want a killed remote run to be resumable, and the best model to
survive box death — without mirroring gigabytes every sync.

## Key facts (from exploring saage/remote/)

- Node layout: `~/.saage_runs/<handoff_id>/` holds `ws/`, scripts, `status.json`
  (heartbeat), `saage.log`, `artifacts/`. The engine checkpoint is separate, at
  `~/.saage/runs/<engine_id>/checkpoint.json`.
- `start.sh` runs `venv/bin/saage run flow/flow.yaml --workspace ws` under tmux,
  with a sidecar (every `sync_interval`: `status running; collect; r2 mirror`) and
  a watchdog (max-runtime killer — stops wedged runs, never restarts).
- `r2push.py` mirrors `artifacts/* + status.json + saage.log`, **re-uploading
  unconditionally**, and **deliberately skips the checkpoint** ("v1").
- `observe.py` reconciles local intent vs node truth (tmux liveness + status.json);
  it already detects "status running but tmux gone" and just prints a ⚠ warning.
- Crash signal: a hard-killed engine (OOM/segfault) cannot run its finalize
  handler, so its checkpoint stays `status=running`; a clean finish is
  `completed`/`failed`. **Checkpoint status, not shell RC, marks a resumable
  crash.**

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Durability | Mirror `checkpoint.json` to R2 every sync (KBs); best model via the `artifacts:` list |
| Mirror cost control | **Changed-only** upload (skip unchanged) → big files upload only when they change (on a *keep*) |
| Cross-box fidelity | "Logical" resume: checkpoint + run branch (code) + R2 model; heavy regenerable artifacts (datasets) re-derived by `cloud_setup` + the loop redoing its current iteration |
| Resume trigger | **Manual only**: `saage remote resume <run> [--target T]` — in place if node alive, from R2 on a fresh target if gone. No auto-resume. |
| Run identity | Engine run-id **pinned** to the handoff id (`--run-id`) so node path, R2 prefix, and `saage resume <id>` all align; one id across boxes |

## Components

### 1. Changed-only R2 mirror (`saage/remote/r2push.py`)

- Add `checkpoint.json` to the upload set. It lives at
  `~/.saage/runs/<run_id>/checkpoint.json`; `r2push` runs in the handoff run dir,
  so it needs the run-id to locate it (see §3). Key: `<prefix>/checkpoint.json`.
- **Changed-only:** maintain a node-side manifest `.r2push_manifest.json`
  (`{key: [size, mtime]}`) in the run dir. Upload a file only if absent/changed;
  rewrite the manifest after. Unchanged syncs upload nothing.
- Best model: no r2push change needed — a flow lists the model path in `artifacts:`;
  the sidecar's `collect()` already stages `artifacts:` matches into `artifacts/`,
  which `r2push` mirrors. Changed-only means it ships only when it changes.
- Cost ≈ `model_size × #keeps + checkpoint(KBs) × #syncs`, not `model_size × #syncs`.
- `collect()` (`scripts.py`) also gets changed-only on its local `cp` into
  `artifacts/` so a large unchanged model is not re-copied every interval.

Wrinkle (documented): `artifacts:` globs are workspace-relative. A flow that writes
its model outside `ws/` (e.g. lewm → `$STABLEWM_HOME`) must land the promoted-best
under a mirrored path. Mechanism stays ws-relative; lewm needs a small flow tweak
(out of scope here — greenfield/fashion-mnist writes under `ws/`, so the test path
is covered).

### 2. `--run-id` for the engine (`saage/cli.py`, `saage/checkpoint.py`)

`saage run` gains `--run-id <id>` (and `SAAGE_RUN_ID` env). When given, the run's
checkpoint uses that id instead of `new_run_id()`. Deterministic node path + R2
prefix + a known id for `saage resume <id>`. Defaults to `new_run_id()` (today's
behavior) when absent. Reject reuse only if a *completed* checkpoint with that id
already exists locally (avoid clobber); otherwise create/resume as normal.

### 3. Node scripts (`saage/remote/scripts.py`)

- `start.sh`: pass `--run-id <handoff_id>` to `saage run` so the engine checkpoint
  lands at `~/.saage/runs/<handoff_id>/`. Mirror that path in the sidecar's
  `r2push` env (`SAAGE_RUN_ID=<handoff_id>` so r2push finds the checkpoint).
- New `resume_sh(spec)`: a `start.sh` variant whose engine line is
  `saage resume <run_id> --workspace "$PWD/ws"` (same sidecar/watchdog/status
  wrapper, same finalize). Used by `saage remote resume`.
- New `restore_from_r2()` step in the cross-box bootstrap: pull
  `<prefix>/checkpoint.json` → `~/.saage/runs/<id>/checkpoint.json`, and pull
  mirrored model artifact(s) → their ws path, before `resume_sh` runs.

### 4. `saage remote resume` (`saage/remote/cli.py`, `observe.py`, `handoff.py`)

`saage remote resume <run> [--target T] [--workspace DIR]`:
1. Load laptop run state (`~/.saage/runs/<run>/` manifest: flow, target, ws mode,
   settings, secrets needed).
2. Probe node liveness (reuse observe reconcile: tmux session + status.json).
3. **In place** (original node alive, run not actively running): push `resume.sh`,
   relaunch under tmux, re-attach sidecar/watchdog. Workspace + node checkpoint
   intact — just re-run the engine as `saage resume <id>`.
4. **Cross-box** (node gone, or `--target` given): on the target — `bootstrap.sh`
   (clone run branch + `cloud_setup`) + `restore_from_r2` (checkpoint + model) +
   `resume.sh`. Per-run secrets (`run_env`) pushed exactly like handoff.
   `--workspace` override aligns the engine checkpoint's recorded ws path to the
   new box (local resume already supports this).
5. Same `run_id` throughout → status/artifacts stay under one R2 prefix across
   boxes.

### 5. status / observe (`saage/remote/observe.py`, `scripts.py`)

- Add a `resuming` phase to `status.json`; `saage remote status` displays it.
- The existing "running but tmux gone" orphan warning suggests
  `saage remote resume <run>`.

## Out of scope

- Auto-resume (in-node or box-death). Manual trigger only.
- Full-workspace mirror. Only checkpoint + declared `artifacts:` files.
- Auto box-death detection / auto-provisioning a replacement.
- lewm's outside-`ws/` model path (needs a separate per-flow tweak).

## Testing

**Offline (CI):**
- `r2push`: `plan_uploads` includes `checkpoint.json`; changed-only manifest skips
  unchanged + re-uploads changed (extend `tests/remote/test_r2.py` seam; no live R2).
- `scripts.py`: golden-string tests for `start.sh` (carries `--run-id`),
  `resume_sh` (runs `saage resume <id>`), and the restore step — mirror existing
  `tests/remote/test_scripts.py`.
- `--run-id`: `build_flow`/CLI honor it (checkpoint written under the given id);
  `tests/test_checkpoint.py` / a CLI test.
- Resume re-entry already covered by `tests/integration/test_resume.py`.

**Live (manual / gated):**
- Headline acceptance test (this iteration's goal): **Fashion-MNIST greenfield on
  Lambda** — handoff with R2 mirror on + small epochs; let baseline + ≥1 hill-climb
  keep produce a mirrored best model; **terminate the box** (simulate death);
  `saage remote resume <run> --target <fresh>`; verify checkpoint + best model
  restored from R2 and the run continues from its iteration; then terminate.
  Requires Lambda API key + R2 creds in `~/.saage/credentials.toml`.

## Prerequisites for the live test (not yet configured)

`~/.saage/credentials.toml` currently has no `[lambda]` key, no `[storage]` (R2),
no targets. The live Lambda test is blocked until those are added
(`saage remote init` + Lambda key + an R2 `[storage]` section). All implementation
and offline tests proceed without them.
