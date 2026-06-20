# Remote Checkpoint & Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a remote run's checkpoint + best model durable in R2 (changed-only), pin the engine run-id to the handoff id, and add `saage remote resume` to continue a killed run — in place if the node lives, on a fresh box from R2 if gone.

**Architecture:** The engine already checkpoints on the node. We (1) make `saage run` honor `SAAGE_RUN_ID` so the node checkpoint id == handoff id; (2) extend `r2push` to mirror `checkpoint.json` with changed-only uploads (best model rides the existing `artifacts:` list); (3) add an R2 pull + a `resume.sh` node script; (4) add a `saage remote resume` command that probes node liveness and relaunches the engine as `saage resume`.

**Tech Stack:** Python 3.10+, boto3 (R2/S3), bash node scripts, pytest. No new deps (boto3 already in `[dev]`/`[r2]`).

## Global Constraints

- Run tests with `python -m pytest` (not bare `pytest`) in this environment.
- No live R2/SSH/Lambda in CI — offline tests use boto stubs / fakes; live paths gated by `SAAGE_SSH_TESTS` / run manually.
- Node run dir = `~/.saage_runs/<run_id>/`; engine checkpoint = `~/.saage/runs/<run_id>/checkpoint.json` (same `<run_id>` after pinning). R2 prefix = `runs/<run_id>`.
- Secrets only ever in `run_env` (0600); never in scripts/config/logs.
- Changed-only mirror must never re-upload an unchanged big file.

**Spec:** `docs/superpowers/specs/2026-06-20-remote-checkpoint-resume-design.md`

---

## Task 1: Engine honors `SAAGE_RUN_ID` / `--run-id`

Pin the engine checkpoint id so node path + R2 prefix + `saage resume <id>` align. `run_env` already exports `SAAGE_RUN_ID=<handoff_id>` (handoff.py:80), so honoring the env is enough for the node; `--run-id` is the explicit CLI form.

**Files:**
- Modify: `saage/cli.py` (the `run` subparser + the `main` run path)
- Test: `tests/test_cli_resume.py`

**Interfaces:**
- Produces: `saage run --run-id <id>` and `SAAGE_RUN_ID=<id>` both cause the run's checkpoint to use `<id>` instead of `new_run_id()`.

- [ ] **Step 1: Write failing tests** — append to `tests/test_cli_resume.py`:

```python
def test_run_id_flag_pins_checkpoint_id(tmp_path):
    f = _command_flow(tmp_path)
    main(["run", str(f), "--workspace", str(tmp_path), "-q", "--run-id", "pinned-123"])
    assert ckpt.Checkpoint("pinned-123").load()["status"] == "completed"


def test_run_id_env_pins_checkpoint_id(tmp_path, monkeypatch):
    f = _command_flow(tmp_path)
    monkeypatch.setenv("SAAGE_RUN_ID", "env-456")
    main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    assert ckpt.Checkpoint("env-456").load()["status"] == "completed"
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/test_cli_resume.py -k run_id -q` → FAIL (`--run-id` unknown / checkpoint id is random).

- [ ] **Step 3: Implement.** In `saage/cli.py` `_build_parser`, add to the `run` subparser (near `--config`):

```python
    run.add_argument("--run-id", dest="run_id", default=None,
                     help="pin the checkpoint/run id (default: auto; also honors "
                          "$SAAGE_RUN_ID — used by `saage remote` for resumability)")
```

In `main`, replace `run_id = ckpt.new_run_id()` with:

```python
    run_id = args.run_id or os.environ.get("SAAGE_RUN_ID") or ckpt.new_run_id()
```

(`os` is already imported in cli.py.)

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/test_cli_resume.py -k run_id -q`.

- [ ] **Step 5: Full suite + commit** — `python -m pytest -q`; then:

```bash
git add saage/cli.py tests/test_cli_resume.py
git commit -m "feat(engine): saage run honors --run-id / SAAGE_RUN_ID to pin checkpoint id"
```

---

## Task 2: `r2push` mirrors the checkpoint, changed-only

**Files:**
- Modify: `saage/remote/r2push.py`
- Test: `tests/remote/test_r2.py`

**Interfaces:**
- Consumes: `SAAGE_RUN_ID` env (to locate `~/.saage/runs/<id>/checkpoint.json`).
- Produces: `plan_uploads(run_dir, prefix, run_id=None)` now includes the checkpoint pair when it exists; `main()` uploads only files whose (size, mtime) changed since the last push, tracked in `<run_dir>/.r2push_manifest.json`.

- [ ] **Step 1: Write failing tests** — add to `tests/remote/test_r2.py` (mirror its existing style; if it stubs boto, reuse that seam). Tests for the pure planning/changed-only logic, no live R2:

```python
import json
from pathlib import Path
from saage.remote import r2push


def test_plan_uploads_includes_checkpoint(tmp_path, monkeypatch):
    # node layout: run dir + ~/.saage/runs/<id>/checkpoint.json
    run = tmp_path / "rundir"; (run / "artifacts").mkdir(parents=True)
    (run / "status.json").write_text("{}")
    home = tmp_path / "home"
    monkeypatch.setenv("SAAGE_HOME", str(home / ".saage"))
    ckdir = home / ".saage" / "runs" / "r1"; ckdir.mkdir(parents=True)
    (ckdir / "checkpoint.json").write_text('{"status":"running"}')
    keys = [k for _, k in r2push.plan_uploads(run, "runs/r1", run_id="r1")]
    assert "runs/r1/checkpoint.json" in keys
    assert "runs/r1/status.json" in keys


def test_changed_only_skips_unchanged(tmp_path):
    run = tmp_path / "rundir"; (run / "artifacts").mkdir(parents=True)
    big = run / "artifacts" / "model.pt"; big.write_bytes(b"x" * 1000)
    man = run / ".r2push_manifest.json"
    pairs = [(big, "runs/r1/artifacts/model.pt")]
    todo1 = r2push.changed(pairs, man)
    assert todo1 == pairs                      # first time: upload
    r2push.record(todo1, man)
    assert r2push.changed(pairs, man) == []    # unchanged: skip
    big.write_bytes(b"y" * 2000)               # changed size
    assert r2push.changed(pairs, man) == pairs # changed: upload again
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/remote/test_r2.py -k "checkpoint or changed" -q`.

- [ ] **Step 3: Implement.** Edit `saage/remote/r2push.py`:

Change `plan_uploads` to also include the checkpoint:

```python
def plan_uploads(run_dir: Path, prefix: str, run_id: str | None = None) -> list[tuple[Path, str]]:
    """(local file, bucket key) pairs for everything worth mirroring."""
    pairs: list[tuple[Path, str]] = []
    artifacts = run_dir / "artifacts"
    if artifacts.is_dir():
        for p in sorted(artifacts.iterdir()):
            if p.is_file():
                pairs.append((p, f"{prefix}/artifacts/{p.name}"))
    for name in ("status.json", "saage.log"):
        p = run_dir / name
        if p.is_file():
            pairs.append((p, f"{prefix}/{name}"))
    run_id = run_id or os.environ.get("SAAGE_RUN_ID")
    if run_id:
        from saage.paths import runs_dir
        ck = runs_dir() / run_id / "checkpoint.json"
        if ck.is_file():
            pairs.append((ck, f"{prefix}/checkpoint.json"))
    return pairs
```

Add changed-only helpers:

```python
def _sig(p: Path) -> list:
    st = p.stat()
    return [st.st_size, int(st.st_mtime)]


def changed(pairs: list[tuple[Path, str]], manifest: Path) -> list[tuple[Path, str]]:
    """Subset of pairs whose (size, mtime) differs from the manifest."""
    try:
        seen = json.loads(manifest.read_text())
    except (OSError, ValueError):
        seen = {}
    return [(p, k) for (p, k) in pairs if seen.get(k) != _sig(p)]


def record(pairs: list[tuple[Path, str]], manifest: Path) -> None:
    try:
        seen = json.loads(manifest.read_text())
    except (OSError, ValueError):
        seen = {}
    for p, k in pairs:
        seen[k] = _sig(p)
    tmp = manifest.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen))
    tmp.replace(manifest)
```

Add `import json` at the top. Then in `main()`, upload only changed and record:

```python
    run_dir = Path.cwd()
    pairs = plan_uploads(run_dir, prefix)
    manifest = run_dir / ".r2push_manifest.json"
    todo = changed(pairs, manifest)
    for local, key in todo:
        client.upload_file(str(local), bucket, key)
    record(todo, manifest)
    print(f"r2push: {len(todo)}/{len(pairs)} changed -> s3://{bucket}/{prefix}/")
    return 0
```

Update the module docstring line that says checkpoints stay on the node / re-uploads unconditionally (now: includes checkpoint, changed-only).

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/remote/test_r2.py -q`.

- [ ] **Step 5: Full suite + commit:**

```bash
git add saage/remote/r2push.py tests/remote/test_r2.py
git commit -m "feat(remote): mirror checkpoint.json to R2, changed-only uploads"
```

---

## Task 3: R2 pull helper (`r2pull`) for cross-box restore

**Files:**
- Create: `saage/remote/r2pull.py`
- Test: `tests/remote/test_r2.py`

**Interfaces:**
- Produces: `python -m saage.remote.r2pull --run-id <id> --run-dir <dir>` — downloads `<prefix>/checkpoint.json` → `~/.saage/runs/<id>/checkpoint.json` and `<prefix>/artifacts/*` → `<run-dir>/restored_artifacts/`. Uses the same `SAAGE_R2_*` env as r2push. Exits 0 on success, 1 on misconfig.

- [ ] **Step 1: Write failing test** — add to `tests/remote/test_r2.py` (use the existing boto-stub seam; assert the planned downloads, not a live pull):

```python
def test_plan_downloads_checkpoint_and_artifacts():
    from saage.remote import r2pull
    keys = r2pull.plan_downloads("runs/r1", ["checkpoint.json", "artifacts/model.pt"])
    assert ("runs/r1/checkpoint.json", "checkpoint") in keys
    assert ("runs/r1/artifacts/model.pt", "artifact") in keys
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/remote/test_r2.py -k downloads -q`.

- [ ] **Step 3: Implement `saage/remote/r2pull.py`:**

```python
#!/usr/bin/env python3
"""Node-side restore: pull a run's checkpoint + artifacts from R2/S3.

Used by cross-box `saage remote resume` to seed a fresh box before
`saage resume`. Same SAAGE_R2_* env as r2push (sourced from run_env).

    python -m saage.remote.r2pull --run-id <id> --run-dir <dir>

Checkpoint -> ~/.saage/runs/<id>/checkpoint.json (where `saage resume` reads it).
Artifacts  -> <run-dir>/restored_artifacts/<name> (the resume flow stages the
best model from here to its workspace path).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def plan_downloads(prefix: str, names: list[str]) -> list[tuple[str, str]]:
    """(bucket key, kind) for each name; kind is 'checkpoint' or 'artifact'."""
    out: list[tuple[str, str]] = []
    for n in names:
        kind = "checkpoint" if n == "checkpoint.json" else "artifact"
        out.append((f"{prefix}/{n}", kind))
    return out


def _client(endpoint: str):
    import boto3
    return boto3.client("s3", endpoint_url=endpoint, region_name="auto")


def main() -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    endpoint = os.environ.get("SAAGE_R2_ENDPOINT")
    bucket = os.environ.get("SAAGE_R2_BUCKET")
    prefix = os.environ.get("SAAGE_R2_PREFIX")
    if not (endpoint and bucket and prefix):
        print("r2pull: SAAGE_R2_* not configured", file=sys.stderr)
        return 1
    try:
        client = _client(endpoint)
    except ModuleNotFoundError:
        print("r2pull: boto3 not installed", file=sys.stderr)
        return 1

    from saage.paths import runs_dir
    ck_dir = runs_dir() / args.run_id
    ck_dir.mkdir(parents=True, exist_ok=True)
    restored = Path(args.run_dir) / "restored_artifacts"
    restored.mkdir(parents=True, exist_ok=True)

    # list artifact keys under the prefix
    names = ["checkpoint.json"]
    resp = client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/artifacts/")
    for obj in resp.get("Contents", []):
        names.append("artifacts/" + obj["Key"].rsplit("/", 1)[-1])

    pulled = 0
    for key, kind in plan_downloads(prefix, names):
        dest = (ck_dir / "checkpoint.json") if kind == "checkpoint" \
            else (restored / key.rsplit("/", 1)[-1])
        try:
            client.download_file(bucket, key, str(dest))
            pulled += 1
        except Exception as exc:                  # missing object is non-fatal
            print(f"r2pull: skip {key}: {exc}", file=sys.stderr)
    print(f"r2pull: restored {pulled} object(s) from s3://{bucket}/{prefix}/")
    return 0 if (ck_dir / "checkpoint.json").is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/remote/test_r2.py -k downloads -q`.

- [ ] **Step 5: Commit:**

```bash
git add saage/remote/r2pull.py tests/remote/test_r2.py
git commit -m "feat(remote): r2pull — restore checkpoint + artifacts from R2 for cross-box resume"
```

---

## Task 4: `resume.sh` node script + collect changed-only

**Files:**
- Modify: `saage/remote/scripts.py`
- Test: `tests/remote/test_scripts.py`

**Interfaces:**
- Produces: `resume_sh(spec: RunSpec) -> str` — like `start_sh` but the engine line is `venv/bin/saage resume "$SAAGE_RUN_ID" --workspace "$PWD/ws"`; same sidecar/watchdog/status/finalize. `start_sh` unchanged except it already gets `SAAGE_RUN_ID` from `run_env` (engine honors it via Task 1).

- [ ] **Step 1: Write failing test** — add to `tests/remote/test_scripts.py`:

```python
def test_resume_sh_runs_saage_resume():
    from saage.remote.scripts import RunSpec, resume_sh
    spec = RunSpec(run_id="r1", flow_file="flow.yaml", ws_mode="branch")
    s = resume_sh(spec)
    assert "saage resume" in s
    assert '"$SAAGE_RUN_ID"' in s or "r1" in s
    assert "--workspace" in s
    assert "status running" in s            # same heartbeat wrapper as start.sh


def test_start_sh_engine_gets_run_id_from_env():
    from saage.remote.scripts import RunSpec, start_sh
    # SAAGE_RUN_ID is sourced from run_env; engine honors it (Task 1). The script
    # must source run_env before the saage run line.
    s = start_sh(RunSpec(run_id="r1", flow_file="flow.yaml", ws_mode="ephemeral"))
    assert s.index("source ./run_env") < s.index("saage run")
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/remote/test_scripts.py -k "resume_sh or run_id" -q`.

- [ ] **Step 3: Implement.** In `saage/remote/scripts.py`, add `resume_sh`. It is `start_sh` with the engine line changed; factor the shared wrapper to avoid duplication — simplest: a private `_run_body(spec, engine_line)` used by both. Add:

```python
def resume_sh(spec: RunSpec) -> str:
    """start.sh, but resume the engine from the node-side checkpoint instead of a
    fresh run. SAAGE_RUN_ID comes from run_env (the pinned id)."""
    engine = (f'venv/bin/saage resume "$SAAGE_RUN_ID" --workspace "$PWD/ws" '
              f'> saage.log 2>&1')
    return _run_body(spec, engine)
```

Refactor `start_sh` to build its engine line and delegate to `_run_body` (keep the existing `--run-id` not needed — env pins it; but `--set`/`--venv`/`run_branch` flags still apply to `start_sh`'s `saage run` line). Keep `start_sh`'s existing body verbatim inside `_run_body` with the engine line parameterized. `resume_sh` does NOT pass `--set`/`--venv` (resume restores them from the checkpoint).

Also make `collect()` skip re-copying an unchanged artifact into `artifacts/` (guard the `cp` with a size+mtime check) so a large unchanged model is not re-copied each interval:

```bash
  for pat in {files}; do
    for f in ws/$pat; do
      [ -f "$f" ] || continue
      dst="artifacts/$(basename "$f")"
      # skip if size+mtime unchanged (avoids re-copying a big unchanged model)
      [ -f "$dst" ] && [ "$f" -ot "$dst" -o "$f" -nt "$dst" ] || { cp -f "$f" "$dst" 2>/dev/null; continue; }
      [ "$(stat -c%s "$f")" = "$(stat -c%s "$dst")" ] || cp -f "$f" "$dst" 2>/dev/null
    done
  done
```

(Keep it simple and correct; the changed-only *upload* in Task 2 is the primary cost control — this just avoids a redundant local copy.)

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/remote/test_scripts.py -q`.

- [ ] **Step 5: Full suite + commit:**

```bash
git add saage/remote/scripts.py tests/remote/test_scripts.py
git commit -m "feat(remote): resume.sh node script; collect skips unchanged copies"
```

---

## Task 5: `saage remote resume` command (orchestration)

**Files:**
- Create: `saage/remote/resume.py`
- Modify: `saage/remote/cli.py` (subparser + dispatch), `saage/remote/target.py` (launch a named script under tmux)
- Test: `tests/remote/test_resume_remote.py`

**Interfaces:**
- Consumes: `RunState` (manifest: flow, target, ws mode, repo, run_branch), `SshTarget` (`session_alive`, `read_status`, `conn`, `run_dir`, `start_script`), `resume_sh`, `r2pull`, `_collect_secrets`.
- Produces: `resume_run(run_ref, *, target_name=None, workspace=None) -> RunState`. Decision: node alive + not running → in-place; else cross-box on `target_name` (required when node gone).

- [ ] **Step 1: Write failing test** (offline — the decision + push sequence with a fake target). Create `tests/remote/test_resume_remote.py`:

```python
import pytest
from saage.remote import resume as rresume


class FakeConn:
    def __init__(self): self.calls = []
    def run(self, cmd, **kw):
        self.calls.append(cmd)
        class P: returncode = 0; stdout = ""; stderr = ""
        return P()
    def write_file(self, path, content, mode=None): self.calls.append(("write", path))
    def rsync_to(self, *a, **k): self.calls.append(("rsync", a))
    dest = "fake"


def test_resume_in_place_when_node_alive(monkeypatch, tmp_path):
    # node alive + run not actively running -> push resume.sh + launch, no r2pull
    decision = rresume.decide(node_alive=True, session_running=False, have_target=False)
    assert decision == "in_place"


def test_resume_cross_box_when_node_gone(monkeypatch):
    decision = rresume.decide(node_alive=False, session_running=False, have_target=True)
    assert decision == "cross_box"


def test_resume_refuses_running_run():
    with pytest.raises(rresume.ResumeError):
        rresume.decide(node_alive=True, session_running=True, have_target=False)


def test_resume_cross_box_needs_target():
    with pytest.raises(rresume.ResumeError):
        rresume.decide(node_alive=False, session_running=False, have_target=False)
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/remote/test_resume_remote.py -q`.

- [ ] **Step 3: Implement `saage/remote/resume.py`.** Pure decision function + orchestration. The orchestration reuses handoff's push patterns — **read `saage/remote/handoff.py` (push block, `_collect_secrets`) and `target.py` for the `conn`/`start` API before writing it.**

```python
"""`saage remote resume` — continue a killed run from its checkpoint.

In place if the original node is alive (workspace + node checkpoint intact);
on a fresh --target from the R2 mirror (checkpoint + artifacts) if the node is
gone. Same run_id throughout, so status/artifacts stay under one R2 prefix.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .creds import Target, get_target, storage_config
from .handoff import (HandoffError, _collect_secrets, _engine_root,
                      ENGINE_EXCLUDES, _load_flow)
from .scripts import RunSpec, bootstrap_sh, resume_sh, stop_sh
from .state import find_run
from .target import SshTarget
from .workspace import WorkspacePlan

log = logging.getLogger("saage.remote")


class ResumeError(RuntimeError):
    pass


def decide(*, node_alive: bool, session_running: bool, have_target: bool) -> str:
    if session_running:
        raise ResumeError("run is still active (tmux session alive) — "
                          "`saage remote kill` it first if you mean to restart")
    if node_alive:
        return "in_place"
    if not have_target:
        raise ResumeError("original node is unreachable — pass --target <name> to "
                          "resume on a fresh box from the R2 checkpoint")
    return "cross_box"
```

Then `resume_run(run_ref, *, target_name=None, workspace=None)`:
1. `rs = find_run(run_ref)`; read `manifest = rs.manifest()` (flow path, original target, ws mode, repo, run_branch) and `state = rs.state()`.
2. Probe original node: `orig = SshTarget(get_target(manifest["target"]))`; `node_alive = orig.conn.ok("true")` (wrap SSHError → False); `session_running = orig.session_alive(rs.run_id)`.
3. `mode = decide(node_alive=node_alive, session_running=session_running, have_target=bool(target_name))`.
4. Rebuild a `RunSpec` from the manifest (same `run_id`, `flow_file`, `ws_mode`, `set_args` from `manifest["set"]`, `artifacts`). Storage must be configured (`storage_config()`), else `ResumeError`.
5. Rebuild secrets via `_collect_secrets(...)` using a `WorkspacePlan`-like view from the manifest's `workspace` block (run_branch, repo_url). (For cross-box this needs the run branch — the manifest records it.)
6. **in_place:** `node = orig`. Push `run_env` + `resume.sh` (+ re-push engine/flow if missing). Launch: `node.start_script(rs.run_id, "resume.sh")`.
7. **cross_box:** `node = SshTarget(get_target(target_name))`; `node.preflight()`. Push engine + flow + `run_env` + `bootstrap.sh`/`resume.sh`/`stop.sh` (same as handoff push). Run `bootstrap.sh` (clones run branch + cloud_setup if `ws_setup` in manifest). Run `r2pull` on the node:
   `conn.run(f"cd $HOME/{rdir} && set -a; source ./run_env; set +a; venv/bin/python -m saage.remote.r2pull --run-id {rs.run_id} --run-dir $PWD")`.
   Stage restored best model from `restored_artifacts/` into `ws/` (per the flow's `artifacts:` paths — copy each restored file to its ws-relative path). Launch `node.start_script(rs.run_id, "resume.sh")`.
8. `rs.update(phase="resuming", target=node.target.name)`; `rs.event("resume", mode=mode)`; return rs.

In `saage/remote/target.py`, generalize launch:

```python
    def start(self, run_id: str) -> None:
        self.start_script(run_id, "start.sh")

    def start_script(self, run_id: str, script: str) -> None:
        session = shlex.quote(f"saage-{run_id}")
        self.conn.run(
            f"tmux new-session -d -s {session} "
            f"{shlex.quote(f'bash $HOME/{self.run_dir(run_id)}/{script}')}"
        )
```

In `saage/remote/cli.py`, add the subparser (after `kill`):

```python
    rr = rsub.add_parser("resume", help="resume a killed run (in place or on a fresh box)")
    rr.add_argument("run", help="run id or prefix")
    rr.add_argument("--target", default=None,
                    help="resume on this target (required if the original node is gone)")
    rr.add_argument("--workspace", default=None, help="override the workspace dir")
```

and dispatch:

```python
    if cmd == "resume":
        from .resume import resume_run
        rs = resume_run(args.run, target_name=args.target, workspace=args.workspace)
        print(f"run {rs.run_id} resuming on {rs.state().get('target')} — "
              f"`saage remote status {rs.run_id}`")
        return 0
```

Add `ResumeError` to `_ERRORS` in cli.py.

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/remote/test_resume_remote.py -q`.

- [ ] **Step 5: Full suite + commit:**

```bash
git add saage/remote/resume.py saage/remote/cli.py saage/remote/target.py tests/remote/test_resume_remote.py
git commit -m "feat(remote): saage remote resume — in-place or cross-box from R2"
```

---

## Task 6: observe — `resuming` phase + orphan hint

**Files:**
- Modify: `saage/remote/observe.py`, `saage/remote/scripts.py` (status fn comment), tests in `tests/remote/test_observe.py`

**Interfaces:**
- `resuming` is a non-final phase; the orphan warning text names `saage remote resume`.

- [ ] **Step 1: Write failing test** — add to `tests/remote/test_observe.py` (match its style; assert the orphan-warning string includes the command):

```python
def test_orphan_warning_suggests_resume(capsys):
    # reuse the module's existing orphan path; assert the hint text
    import saage.remote.observe as observe
    assert "saage remote resume" in observe._ORPHAN_HINT
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/remote/test_observe.py -k orphan -q`.

- [ ] **Step 3: Implement.** In `observe.py`, extract the orphan-warning text to a constant and reference `saage remote resume`:

```python
_ORPHAN_HINT = ("⚠  node status says running but the tmux session is gone — the "
                "run likely crashed; `saage remote resume <run>` to continue it")
```

Use `_ORPHAN_HINT` where the current inline warning is printed (status + reconcile paths). `resuming` is already non-final (only `_FINAL` phases are terminal), so no change needed there beyond confirming `resuming` is not in `_FINAL`.

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/remote/test_observe.py -q`.

- [ ] **Step 5: Full suite + commit:**

```bash
git add saage/remote/observe.py tests/remote/test_observe.py
git commit -m "feat(remote): status orphan warning points at saage remote resume"
```

---

## Task 7: Docs

**Files:**
- Modify: `README.md` (remote section), `docs/remote_handoff_plan.md` (note the new resume + R2 checkpoint mirror)

- [ ] **Step 1: README.** In the `## Remote handoff (saage remote)` section, after the command list, add:

```markdown
A killed remote run is resumable. The engine checkpoint (and any file listed in
the flow's `artifacts:`, e.g. the best model) is mirrored to R2 each sync
(changed-only — big files upload only when they change). Then:

\`\`\`bash
saage remote resume <run>                 # node still up: resume in place
saage remote resume <run> --target spark  # node gone: fresh box, from the R2 checkpoint
\`\`\`

Cross-box resume restores the checkpoint + mirrored artifacts from R2 and
reconstructs code from the run branch; heavy regenerable inputs (datasets) are
re-staged by the flow's `cloud_setup`, and the hill-climb continues from its
recorded `best_score`/iteration. To keep the trained best model across a box
death, list its (workspace-relative) path in the flow's `artifacts:`.
```

(Write real triple-backtick fences.)

- [ ] **Step 2: Commit:**

```bash
git add README.md docs/remote_handoff_plan.md
git commit -m "docs: document remote resume + R2 checkpoint/model mirroring"
```

---

## Self-Review

**Spec coverage:** §Components 1 (changed-only mirror + checkpoint) → Tasks 2,4; 2 (`--run-id`) → Task 1; 3 (scripts: start.sh env, resume.sh, restore) → Tasks 1,3,4; 4 (`saage remote resume`) → Task 5; 5 (observe) → Task 6; docs → Task 7. R2 pull (restore) → Task 3. All covered.

**Placeholder scan:** none — every step has concrete code/commands. (Task 5 orchestration explicitly instructs reading handoff.py/target.py for the conn API, then gives the structure + key code; the decision fn is fully coded and tested.)

**Type/name consistency:** `plan_uploads(run_dir, prefix, run_id=None)`, `changed`/`record`/`_sig` (Task 2) reused nowhere else; `resume_sh(spec)` (Task 4) consumed by Task 5; `decide(...)`/`resume_run(...)`/`ResumeError` (Task 5) consumed by cli dispatch; `start_script(run_id, script)` (Task 5 target.py) used by resume. `SAAGE_RUN_ID` honored in Task 1, set by handoff (existing), read by r2push (Task 2) + resume.sh (Task 4). Consistent.

**Live acceptance test (post-merge-of-offline-green, manual/autonomous):** Fashion-MNIST greenfield on a Lambda A10 — handoff (R2 on) → baseline + ≥1 keep (best model mirrored) → terminate box → `saage remote resume <run> --target <fresh a10>` → verify checkpoint + best model restored, run continues → terminate. Keep epochs small; terminate every box.
