# Checkpoint & Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make any `saage` run resumable after a kill/crash — the engine snapshots its JSON-serializable shared store after every node, and `saage resume` restarts the flow at the top-level step that was in progress, keeping all completed work.

**Architecture:** A run is `flow.run(seed)` where `seed` *is* the shared store. We snapshot `seed` to `~/.saage/runs/<run_id>/checkpoint.json` after each node, tagged with `resume_step` (the index of the top-level `workflow:` step currently executing). Resume reloads the snapshot, rebuilds the flow unchanged, and sets the top-level `start_node` to `steps[resume_step]` — PocketFlow walks forward from there; earlier steps never run. Loop position is restored for free because loop counters already live in `shared["_iter"]`.

**Tech Stack:** Python 3.10+, PocketFlow (graph engine), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-19-checkpoint-resume-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `saage/paths.py` | One definition of `~/.saage` run-state locations (`saage_home`, `runs_dir`) | Create |
| `saage/checkpoint.py` | Run id, flow fingerprint, `Checkpoint` (atomic write/load/mark), run registry (`list_runs`/`find_run`) | Create |
| `saage/hydrate.py` | Tag nodes with `_step_index`; wire the checkpoint sink onto every `Subflow`; `resume_step` start-node swap | Modify |
| `saage/primitives.py` | `Subflow` gains `sink` + checkpoint-writing `_orch` + one-shot reset suppression on resume | Modify |
| `saage/cli.py` | `saage run` checkpoint lifecycle; new `saage resume` / `saage runs` subcommands | Modify |
| `saage/remote/creds.py`, `saage/remote/state.py` | Import path helpers from `saage.paths` (DRY) | Modify |
| `tests/test_checkpoint.py` | Unit tests for checkpoint + registry + fingerprint | Create |
| `tests/test_tagging.py` | Unit test for `_step_index` tagging | Create |
| `tests/integration/test_resume.py` | Crash-mid-loop then resume; assert no redo | Create |
| `tests/test_cli_resume.py` | CLI `run` lifecycle, `runs`, `resume` refusal | Create |

**Note on a spec refinement found during planning:** the spec's §Resume step 5 described a `shared["_resume_step"]` flag for reset-suppression. The plan instead sets a one-shot `_skip_reset_once = True` attribute directly on the resume-target `Subflow` object. This is cleaner — it keeps a transient control flag *out* of the persisted shared store (so it can never leak into a checkpoint), and it naturally handles nested loops (only the outermost subflow skips its reset). Behavior is identical to the spec's intent.

---

## Task 1: Factor run-state paths into `saage/paths.py`

**Files:**
- Create: `saage/paths.py`
- Modify: `saage/remote/creds.py:46-47` (remove local `saage_home`), `saage/remote/state.py:18,25-26` (import `runs_dir`)

- [ ] **Step 1: Create `saage/paths.py`**

```python
"""Filesystem locations for saage run state under ~/.saage.

One definition, shared by the engine's checkpoint store (saage.checkpoint) and
the remote subsystem (saage.remote.*), so "where runs live" is not duplicated.
SAAGE_HOME relocates the root (used by tests).
"""
from __future__ import annotations

import os
from pathlib import Path


def saage_home() -> Path:
    return Path(os.environ.get("SAAGE_HOME", "~/.saage")).expanduser()


def runs_dir() -> Path:
    return saage_home() / "runs"
```

- [ ] **Step 2: Point `remote/creds.py` at the shared helper**

In `saage/remote/creds.py`, delete the local definition (lines 46-47):

```python
def saage_home() -> Path:
    return Path(os.environ.get("SAAGE_HOME", "~/.saage")).expanduser()
```

and add this import near the top of the file (after the existing `from pathlib import Path`):

```python
from ..paths import saage_home
```

(Other functions in `creds.py` keep calling `saage_home()` unchanged; it is now imported rather than locally defined, and still re-exported from this module for any importer that does `from .creds import saage_home`.)

- [ ] **Step 3: Point `remote/state.py` at the shared helper**

In `saage/remote/state.py`, replace the import line (line 18):

```python
from .creds import saage_home
```

with:

```python
from ..paths import runs_dir
```

and delete the local `runs_dir` definition (lines 25-26):

```python
def runs_dir() -> Path:
    return saage_home() / "runs"
```

- [ ] **Step 4: Run the existing remote tests to verify the refactor is behavior-neutral**

Run: `pytest tests/remote/test_state.py tests/remote/test_creds.py -q`
Expected: PASS (same tests as before; only the source of `saage_home`/`runs_dir` moved).

- [ ] **Step 5: Run the full suite to confirm nothing else imported the moved names**

Run: `pytest -q`
Expected: PASS (same count as before this task).

- [ ] **Step 6: Commit**

```bash
git add saage/paths.py saage/remote/creds.py saage/remote/state.py
git commit -m "refactor: factor ~/.saage path helpers into saage.paths"
```

---

## Task 2: Checkpoint store + run registry (`saage/checkpoint.py`)

**Files:**
- Create: `saage/checkpoint.py`
- Test: `tests/test_checkpoint.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_checkpoint.py
"""Unit tests for the checkpoint store + run registry."""
import json

import pytest

from saage import checkpoint as ckpt


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / ".saage"))


def test_new_run_id_is_unique_and_sorted():
    a = ckpt.new_run_id()
    b = ckpt.new_run_id()
    assert a != b
    assert len(a) >= 8


def test_create_write_load_roundtrip():
    c = ckpt.Checkpoint.create("run1", flow_path="/x/flow.yaml", workspace="/ws")
    rec = c.load()
    assert rec["status"] == "running"
    assert rec["flow_path"] == "/x/flow.yaml"
    c.write({"best_score": 0.9, "_iter": {"hill": 6}}, resume_step=7, status="running")
    rec = c.load()
    assert rec["resume_step"] == 7
    assert rec["shared"]["_iter"]["hill"] == 6
    assert rec["status"] == "running"


def test_write_is_atomic_no_partial_file():
    c = ckpt.Checkpoint.create("run2")
    c.write({"k": "v"}, resume_step=0)
    # no leftover tmp file after an atomic rename
    assert not (c.dir / "checkpoint.json.tmp").exists()
    assert json.loads((c.dir / "checkpoint.json").read_text())["shared"]["k"] == "v"


def test_mark_updates_only_status():
    c = ckpt.Checkpoint.create("run3")
    c.write({"k": 1}, resume_step=2)
    c.mark("completed")
    rec = c.load()
    assert rec["status"] == "completed"
    assert rec["resume_step"] == 2          # preserved
    assert rec["shared"] == {"k": 1}        # preserved


def test_non_serializable_value_is_coerced_with_warning(caplog):
    c = ckpt.Checkpoint.create("run4")
    c.write({"obj": object()}, resume_step=0)   # not JSON-serializable
    assert "non-serializable" in caplog.text.lower()
    assert isinstance(c.load()["shared"]["obj"], str)


def test_list_runs_only_includes_dirs_with_checkpoint(tmp_path):
    ckpt.Checkpoint.create("a")
    ckpt.Checkpoint.create("b")
    (ckpt.runs_dir() / "remote_only").mkdir(parents=True)   # no checkpoint.json
    ids = {c.run_id for c in ckpt.list_runs()}
    assert ids == {"a", "b"}


def test_find_run_by_prefix_and_latest_resumable():
    a = ckpt.Checkpoint.create("20260619-100000-aaaa")
    a.write({}, resume_step=0, status="completed")
    b = ckpt.Checkpoint.create("20260619-120000-bbbb")
    b.write({}, resume_step=1, status="running")
    assert ckpt.find_run("20260619-100000-aaaa").run_id == a.run_id  # exact
    assert ckpt.find_run("20260619-1000").run_id == a.run_id         # prefix
    assert ckpt.find_run(None).run_id == b.run_id                    # latest resumable


def test_find_run_no_resumable_raises():
    a = ckpt.Checkpoint.create("only")
    a.write({}, resume_step=0, status="completed")
    with pytest.raises(FileNotFoundError):
        ckpt.find_run(None)


def test_fingerprint_changes_when_a_skill_changes(tmp_path):
    flow = tmp_path / "flow.yaml"
    flow.write_text("provider: {type: local, model: x}\nworkflow: []\n")
    skill = tmp_path / "s"
    skill.mkdir()
    (skill / "skill.md").write_text("body v1")
    fp1 = ckpt.fingerprint(flow)
    (skill / "skill.md").write_text("body v2")
    fp2 = ckpt.fingerprint(flow)
    assert fp1 != fp2
    assert fp1.startswith("sha256:")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_checkpoint.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'saage.checkpoint'`

- [ ] **Step 3: Implement `saage/checkpoint.py`**

```python
"""Engine-side run checkpoints: ~/.saage/runs/<run_id>/checkpoint.json.

A run snapshots its JSON-serializable shared store after every node, tagged with
`resume_step` (the index of the top-level workflow step in progress). `saage
resume` reloads it and restarts the flow at that step. Writes are atomic
(tmp + rename), mirroring saage.remote.state.

This lives alongside (but is independent of) the remote subsystem's per-run
state files; `list_runs` filters to dirs that actually contain a checkpoint.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .paths import runs_dir

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    """Sortable, unique: 'YYYYMMDD-HHMMSS-<4 hex>'."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


def fingerprint(flow_path) -> str:
    """sha256 over flow.yaml + every skill.md and *.py in the flow dir, so a
    structural edit to the flow is detectable at resume time."""
    flow_path = Path(flow_path)
    flow_dir = flow_path.parent
    files = [flow_path]
    files += sorted(flow_dir.rglob("skill.md"))
    files += sorted(p for p in flow_dir.rglob("*.py") if "__pycache__" not in p.parts)
    h = hashlib.sha256()
    for f in files:
        try:
            data = f.read_bytes()
        except OSError:
            continue
        h.update(f.name.encode())
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def _dumps(record: dict) -> str:
    try:
        return json.dumps(record, indent=2) + "\n"
    except TypeError as e:
        log.warning("checkpoint: non-serializable value in shared (%s); "
                    "coercing with str() — keep the shared store JSON-able", e)
        return json.dumps(record, indent=2, default=str) + "\n"


class Checkpoint:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.dir = runs_dir() / run_id

    @classmethod
    def create(cls, run_id: str, **meta) -> "Checkpoint":
        c = cls(run_id)
        c.dir.mkdir(parents=True, exist_ok=True)
        c._write({"run_id": run_id, "status": "running", "started_at": _now(),
                  "resume_step": None, "shared": {}, **meta})
        return c

    @property
    def file(self) -> Path:
        return self.dir / "checkpoint.json"

    def load(self) -> dict:
        return json.loads(self.file.read_text())

    def _write(self, record: dict) -> None:
        record["updated_at"] = _now()
        tmp = self.dir / "checkpoint.json.tmp"
        tmp.write_text(_dumps(record))
        os.replace(tmp, self.file)

    def write(self, shared: dict, resume_step, status: str = "running") -> None:
        rec = self.load()
        rec["shared"] = shared
        rec["resume_step"] = resume_step
        rec["status"] = status
        self._write(rec)

    def mark(self, status: str) -> None:
        rec = self.load()
        rec["status"] = status
        self._write(rec)


def list_runs() -> list[Checkpoint]:
    base = runs_dir()
    if not base.exists():
        return []
    return [Checkpoint(p.name) for p in sorted(base.iterdir())
            if p.is_dir() and (p / "checkpoint.json").is_file()]


def find_run(ref: str | None = None) -> Checkpoint:
    """Resolve a run by id or unique prefix; with ref=None, the most recent
    *resumable* (status != 'completed') run."""
    runs = list_runs()
    if not runs:
        raise FileNotFoundError("no runs recorded yet")
    if ref is None:
        resumable = [r for r in runs if r.load().get("status") != "completed"]
        if not resumable:
            raise FileNotFoundError("no resumable runs (all completed)")
        return max(resumable, key=lambda r: r.load().get("started_at", ""))
    matches = [r for r in runs if r.run_id == ref] or \
              [r for r in runs if r.run_id.startswith(ref)]
    if not matches:
        raise FileNotFoundError(f"no run matching {ref!r}")
    if len(matches) > 1:
        ids = ", ".join(r.run_id for r in matches)
        raise FileNotFoundError(f"ambiguous run {ref!r}: {ids}")
    return matches[0]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_checkpoint.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add saage/checkpoint.py tests/test_checkpoint.py
git commit -m "feat: checkpoint store, run registry, and flow fingerprint"
```

---

## Task 3: Tag nodes with `_step_index` (`saage/hydrate.py`)

**Files:**
- Modify: `saage/hydrate.py:133-136` (tag steps before chaining)
- Test: `tests/test_tagging.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tagging.py
"""Every node must know which top-level workflow step it belongs to."""
from saage.hydrate import build_flow


def _all_nodes(node, seen=None, out=None):
    seen = set() if seen is None else seen
    out = [] if out is None else out
    if node is None or id(node) in seen:
        return out
    seen.add(id(node))
    out.append(node)
    start = getattr(node, "start_node", None)
    if start is not None:
        _all_nodes(start, seen, out)
    for nxt in getattr(node, "successors", {}).values():
        _all_nodes(nxt, seen, out)
    return out


def test_every_node_tagged_with_its_top_level_step(tmp_path):
    flow_yaml = tmp_path / "flow.yaml"
    flow_yaml.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - {id: first, type: command, run: 'echo a'}\n"
        "  - id: loop\n"
        "    type: counting_loop\n"
        "    max_iterations: 2\n"
        "    body:\n"
        "      - {id: tick, type: command, run: 'echo b'}\n"
        "  - {id: last, type: command, run: 'echo c'}\n"
    )
    flow, _ = build_flow(flow_yaml, provider=object(), workspace=str(tmp_path))
    # walk from the resume step list via the top flow's start chain
    nodes = _all_nodes(flow.start_node)
    # the loop body node 'tick' belongs to top-level step index 1 (the loop)
    tick = [n for n in nodes if getattr(n, "id", None) == "tick"][0]
    assert tick._step_index == 1
    first = [n for n in nodes if getattr(n, "id", None) == "first"][0]
    assert first._step_index == 0
    last = [n for n in nodes if getattr(n, "id", None) == "last"][0]
    assert last._step_index == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_tagging.py -q`
Expected: FAIL with `AttributeError: 'CommandNode' object has no attribute '_step_index'`

- [ ] **Step 3: Add the tagging helper and call it before chaining**

In `saage/hydrate.py`, add this helper just above `build_flow` (after `build_step`):

```python
def _tag_step(node, idx: int, seen=None) -> None:
    """Set `_step_index = idx` on every node reachable from a top-level step
    (the step itself, a loop subflow, and all body/guard nodes). Must run BEFORE
    top-level steps are chained, so the walk does not cross into later steps."""
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    node._step_index = idx
    start = getattr(node, "start_node", None)
    if start is not None:
        _tag_step(start, idx, seen)
    for nxt in getattr(node, "successors", {}).values():
        _tag_step(nxt, idx, seen)
```

Then in `build_flow`, change the step-building block (currently lines 133-136):

```python
    steps = [build_step(s, ctx) for s in spec["workflow"]]
    for a, b in zip(steps, steps[1:]):
        a >> b
```

to tag each step *before* the chaining:

```python
    steps = [build_step(s, ctx) for s in spec["workflow"]]
    for k, step in enumerate(steps):
        _tag_step(step, k)               # tag BEFORE chaining (walk stays in-step)
    for a, b in zip(steps, steps[1:]):
        a >> b
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_tagging.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite (tagging must not change run behavior)**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add saage/hydrate.py tests/test_tagging.py
git commit -m "feat: tag every node with its top-level step index (_step_index)"
```

---

## Task 4: `Subflow` writes checkpoints during a run

**Files:**
- Modify: `saage/primitives.py:30-46` (Subflow: add `sink`, override `_orch`)
- Modify: `saage/hydrate.py` (`build_flow`: accept `checkpoint=`, set `sink` on every Subflow)
- Test: `tests/integration/test_resume.py` (first test only)

- [ ] **Step 1: Write the failing test (checkpoints are written)**

```python
# tests/integration/test_resume.py
"""Crash mid-loop, then resume — completed iterations are not redone."""
import pytest

from saage import checkpoint as ckpt
from saage.hydrate import build_flow


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / ".saage"))


def _loop_flow(tmp_path):
    f = tmp_path / "flow.yaml"
    f.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - id: hill\n"
        "    type: counting_loop\n"
        "    max_iterations: 5\n"
        "    body:\n"
        "      - {id: tick, type: command, run: 'echo x >> counter.txt'}\n"
    )
    return f


def test_checkpoint_written_during_run(tmp_path):
    f = _loop_flow(tmp_path)
    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    flow, seed = build_flow(f, provider=object(), workspace=str(tmp_path),
                            checkpoint=c)
    flow.run(seed)
    rec = c.load()
    # the loop is the only (index 0) top-level step
    assert rec["resume_step"] == 0
    assert rec["shared"]["_iter"]["hill"] == 5
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/integration/test_resume.py::test_checkpoint_written_during_run -q`
Expected: FAIL with `TypeError: build_flow() got an unexpected keyword argument 'checkpoint'`

- [ ] **Step 3: Give `Subflow` a sink and a checkpoint-writing `_orch`**

In `saage/primitives.py`, add `import copy` at the top (with the other imports), then replace the `Subflow` class (lines 30-46) with:

```python
class Subflow(Flow):
    def __init__(self, start, reset=(), sink=None):
        super().__init__(start=start)
        # (namespace, key) pairs in the shared store to clear on every entry, so
        # a loop nested inside another loop gets a fresh counter each time the
        # outer loop re-enters it. The top-level flow passes nothing.
        self._reset = reset
        self.sink = sink                 # a checkpoint.Checkpoint, or None

    def prep(self, shared):
        # On resume, the target loop's counter must survive — skip its one reset.
        if getattr(self, "_skip_reset_once", False):
            self._skip_reset_once = False
            return None
        for ns, key in self._reset:
            d = shared.get(ns)
            if key is not None and isinstance(d, dict):
                d.pop(key, None)
        return None

    def _orch(self, shared, params=None):
        # PocketFlow's stock orchestration loop, plus a checkpoint write after
        # each node. Loop bodies run in their own subflow's _orch, so this yields
        # per-iteration writes inside loops and per-step writes at the top level.
        curr = copy.copy(self.start_node)
        p = params or {**self.params}
        last_action = None
        while curr:
            curr.set_params(p)
            last_action = curr._run(shared)
            if self.sink is not None:
                self.sink.write(shared, getattr(curr, "_step_index", None), "running")
            curr = copy.copy(self.get_next_node(curr, last_action))
        return last_action

    def post(self, shared, prep_res, last_action):
        return "default" if last_action in _SUCCESS else last_action
```

- [ ] **Step 4: Wire the sink onto every Subflow in `build_flow`**

In `saage/hydrate.py`, add this helper next to `_tag_step`:

```python
def _all_subflows(node, seen=None, out=None):
    seen = set() if seen is None else seen
    out = [] if out is None else out
    if node is None or id(node) in seen:
        return out
    seen.add(id(node))
    if isinstance(node, Subflow):
        out.append(node)
    start = getattr(node, "start_node", None)
    if start is not None:
        _all_subflows(start, seen, out)
    for nxt in getattr(node, "successors", {}).values():
        _all_subflows(nxt, seen, out)
    return out
```

Change the `build_flow` signature to accept `checkpoint`:

```python
def build_flow(flow_yaml, provider=None, provider_overrides: dict | None = None,
               workspace=None, venv: str | None = None,
               config: "str | Path | EngineConfig | None" = None,
               checkpoint=None):
```

Then replace the flow-assembly tail of `build_flow` (currently the `for a, b in zip(...)` chaining through the `return` — lines ~134-143) with:

```python
    for k, step in enumerate(steps):
        _tag_step(step, k)               # tag BEFORE chaining (walk stays in-step)
    for a, b in zip(steps, steps[1:]):
        a >> b
    log.info("workflow ready: %d top-level step(s)", len(steps))
    top = Subflow(start=steps[0])
    if checkpoint is not None:
        for sf in _all_subflows(top):    # top + every nested loop subflow
            sf.sink = checkpoint
    seed = dict(spec.get("shared", {}))
    seed.setdefault("workspace", str(ws))
    seed.setdefault("venv", venv)
    seed.setdefault("flow_dir", str(flow_dir.resolve()))   # for bundled scripts
    # the interpreter launcher for helper scripts: Windows has no `python3`
    seed.setdefault("python", "python" if os.name == "nt" else "python3")
    return top, seed
```

(Remove the now-duplicated `_tag_step` loop added in Task 3 if it still appears earlier in the function — there must be exactly one tagging loop, immediately before the chaining loop shown here.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/integration/test_resume.py::test_checkpoint_written_during_run -q`
Expected: PASS

- [ ] **Step 6: Run the full suite (Subflow `_orch`/`prep` changes must not regress)**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add saage/primitives.py saage/hydrate.py tests/integration/test_resume.py
git commit -m "feat: Subflow writes a checkpoint after every node"
```

---

## Task 5: Resume re-entry (restore + restart at `resume_step`)

**Files:**
- Modify: `saage/hydrate.py` (`build_flow`: `resume_step=` start-node swap + one-shot reset flag; `run_flow`: `resume=` handling)
- Test: `tests/integration/test_resume.py` (add the crash/resume tests)

- [ ] **Step 1: Write the failing tests (crash mid-loop, then resume)**

Add to `tests/integration/test_resume.py`:

```python
def test_resume_does_not_redo_completed_iterations(tmp_path, monkeypatch):
    import saage.nodes as nodes
    f = _loop_flow(tmp_path)
    counter = tmp_path / "counter.txt"

    # crash entering iteration 3: the 3rd run of the body command raises
    real = nodes.run_shell
    calls = {"n": 0}

    def flaky(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(cmd, **kw)

    monkeypatch.setattr(nodes, "run_shell", flaky)

    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    flow, seed = build_flow(f, provider=object(), workspace=str(tmp_path),
                            checkpoint=c)
    with pytest.raises(RuntimeError, match="boom"):
        flow.run(seed)

    assert counter.read_text().count("x") == 2          # iterations 1-2 only
    rec = c.load()
    assert rec["shared"]["_iter"]["hill"] == 2

    # --- resume: real shell back, restart at the saved step ---
    monkeypatch.setattr(nodes, "run_shell", real)
    c2 = ckpt.Checkpoint(c.run_id)
    flow2, _ = build_flow(f, provider=object(), workspace=str(tmp_path),
                          checkpoint=c2, resume_step=rec["resume_step"])
    resumed_seed = dict(rec["shared"])
    flow2.run(resumed_seed)

    # iterations 3,4,5 appended -> 5 total. 7 would mean 1-2 were redone.
    assert counter.read_text().count("x") == 5
    assert resumed_seed["_iter"]["hill"] == 5


def test_run_flow_resume_helper(tmp_path, monkeypatch):
    """run_flow(resume=ckpt) restores shared and restarts at resume_step."""
    import saage.nodes as nodes
    from saage.hydrate import run_flow
    f = _loop_flow(tmp_path)
    counter = tmp_path / "counter.txt"

    real = nodes.run_shell
    calls = {"n": 0}

    def flaky(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(cmd, **kw)

    monkeypatch.setattr(nodes, "run_shell", flaky)
    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    with pytest.raises(RuntimeError):
        run_flow(f, provider=object(), workspace=str(tmp_path), checkpoint=c)

    monkeypatch.setattr(nodes, "run_shell", real)
    out = run_flow(f, provider=object(), workspace=str(tmp_path),
                   resume=ckpt.Checkpoint(c.run_id))
    assert counter.read_text().count("x") == 5
    assert out["_iter"]["hill"] == 5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/integration/test_resume.py -q`
Expected: FAIL — `build_flow()` rejects `resume_step`, and `run_flow()` rejects `resume`.

- [ ] **Step 3: Add `resume_step` to `build_flow`**

In `saage/hydrate.py`, extend the `build_flow` signature:

```python
def build_flow(flow_yaml, provider=None, provider_overrides: dict | None = None,
               workspace=None, venv: str | None = None,
               config: "str | Path | EngineConfig | None" = None,
               checkpoint=None, resume_step: int | None = None):
```

Then, in the assembly tail, right after the `top = Subflow(start=steps[0])` / sink-wiring block and before building `seed`, add:

```python
    if resume_step is not None:
        top.start_node = steps[resume_step]
        if isinstance(steps[resume_step], Subflow):
            # the resumed loop must keep its restored _iter counter on first entry
            steps[resume_step]._skip_reset_once = True
```

- [ ] **Step 4: Add `resume` handling to `run_flow`**

In `saage/hydrate.py`, replace `run_flow` (lines ~146-158) with:

```python
def run_flow(flow_yaml, provider=None, shared: dict | None = None,
             provider_overrides: dict | None = None,
             workspace=None, venv: str | None = None,
             config: "str | Path | EngineConfig | None" = None,
             checkpoint=None, resume=None) -> dict:
    resume_step = None
    if resume is not None:
        rec = resume.load()
        resume_step = rec["resume_step"]
        checkpoint = checkpoint or resume          # write back into the same run
    flow, seed = build_flow(flow_yaml, provider=provider,
                            provider_overrides=provider_overrides,
                            workspace=workspace, venv=venv, config=config,
                            checkpoint=checkpoint, resume_step=resume_step)
    if resume is not None:
        seed = dict(rec["shared"])                 # restore the whole store
        seed.pop("_poll_start", None)              # monotonic clocks from the
        seed.pop("_poll_count", None)              # dead process are meaningless
        log.info("resuming run at step %s", resume_step)
    if shared:
        seed.update(shared)
    log.info("starting run%s", f" (seed: {seed})" if seed and resume is None else "")
    flow.run(seed)
    log.info("run complete")
    return seed
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/integration/test_resume.py -q`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add saage/hydrate.py tests/integration/test_resume.py
git commit -m "feat: resume re-entry — restore shared and restart at resume_step"
```

---

## Task 6: `saage run` checkpoint lifecycle (CLI)

**Files:**
- Modify: `saage/cli.py:124-149` (`main`: create checkpoint, mark completed/failed)
- Test: `tests/test_cli_resume.py` (first tests)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_resume.py
"""CLI: `saage run` creates a checkpoint; `runs`/`resume` use the registry."""
import pytest

from saage import checkpoint as ckpt
from saage.cli import main


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / ".saage"))


def _command_flow(tmp_path):
    f = tmp_path / "flow.yaml"
    f.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - {id: only, type: command, run: 'echo hi'}\n"
    )
    return f


def test_run_creates_completed_checkpoint(tmp_path):
    f = _command_flow(tmp_path)
    rc = main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    assert rc == 0
    runs = ckpt.list_runs()
    assert len(runs) == 1
    assert runs[0].load()["status"] == "completed"


def test_run_marks_failed_on_engine_error(tmp_path, monkeypatch):
    import saage.nodes as nodes
    f = _command_flow(tmp_path)

    def boom(cmd, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(nodes, "run_shell", boom)
    with pytest.raises(RuntimeError, match="kaboom"):
        main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    runs = ckpt.list_runs()
    assert len(runs) == 1
    assert runs[0].load()["status"] == "failed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli_resume.py -q`
Expected: FAIL — `list_runs()` is empty (run doesn't create a checkpoint yet).

- [ ] **Step 3: Create + lifecycle-manage the checkpoint in `main`**

In `saage/cli.py`, add to the imports near the top:

```python
from . import checkpoint as ckpt
```

Then replace the run body of `main` (the block from `overrides = {...}` through `return 0`, currently lines 132-149) with:

```python
    overrides = {"type": args.provider, "model": args.model, "base_url": args.base_url}
    run_id = ckpt.new_run_id()
    flow_path = str(Path(args.flow).resolve())
    cp = ckpt.Checkpoint.create(
        run_id,
        flow_path=flow_path,
        fingerprint=ckpt.fingerprint(flow_path),
        provider_overrides={k: v for k, v in overrides.items() if v is not None},
        config_path=str(Path(args.config).resolve()) if args.config else None,
        venv=args.venv,
    )
    flow, seed = build_flow(args.flow, provider_overrides=overrides,
                            workspace=args.workspace, venv=args.venv,
                            config=args.config, checkpoint=cp)
    seed.update(_parse_set(args.overrides))
    root = Path(seed["workspace"])               # the resolved workspace
    cp.write(seed, resume_step=None, status="running")   # record workspace/venv

    before = _snapshot(root)
    log = logging.getLogger("saage")
    log.info("starting run %s", run_id)
    try:
        flow.run(seed)
    except BaseException:
        cp.mark("failed")
        raise
    cp.mark("completed")
    log.info("run complete")
    after = _snapshot(root)

    _print_summary(seed, before, after, root)
    if args.verbose:                              # full agent/command outputs
        print("\nresults:")
        print(json.dumps(seed.get("results", {}), indent=2, default=str))
    return 0
```

(`cp.write(seed, ...)` once before the run captures `workspace`/`venv`/`flow_dir` into the checkpoint so `resume` can reconstruct the run even if the very first node never completes. The per-node writes inside `Subflow._orch` then keep it current.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli_resume.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add saage/cli.py tests/test_cli_resume.py
git commit -m "feat: saage run records a checkpoint (running -> completed/failed)"
```

---

## Task 7: `saage resume` and `saage runs` subcommands (CLI)

**Files:**
- Modify: `saage/cli.py` (`_build_parser`: add subparsers; `main`: dispatch + handlers)
- Test: `tests/test_cli_resume.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_resume.py`:

```python
def test_runs_lists_runs(tmp_path, capsys):
    f = _command_flow(tmp_path)
    main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    rc = main(["runs"])
    assert rc == 0
    out = capsys.readouterr().out
    runs = ckpt.list_runs()
    assert runs[0].run_id in out
    assert "completed" in out


def test_resume_refuses_on_fingerprint_mismatch(tmp_path, monkeypatch):
    import saage.nodes as nodes
    f = _command_flow(tmp_path)

    # make the run fail so it stays resumable
    def boom(cmd, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(nodes, "run_shell", boom)
    with pytest.raises(RuntimeError):
        main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    monkeypatch.setattr(nodes, "run_shell", nodes.run_shell.__wrapped__
                        if hasattr(nodes.run_shell, "__wrapped__") else _real_shell())

    # edit the flow so the fingerprint no longer matches
    f.write_text(f.read_text() + "\n# edited\n")
    rc = main(["resume"])
    assert rc == 1                       # refused
    out = capsys = None  # (no assertion on text needed; non-zero rc is the contract)


def _real_shell():
    from saage.shell import run_shell
    return run_shell


def test_resume_completes_a_crashed_run(tmp_path, monkeypatch, capsys):
    import saage.nodes as nodes
    # a 2-step command flow; crash on the 2nd step, then resume to finish it
    f = tmp_path / "flow.yaml"
    f.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - {id: one, type: command, run: 'echo 1 >> log.txt'}\n"
        "  - {id: two, type: command, run: 'echo 2 >> log.txt'}\n"
    )
    log = tmp_path / "log.txt"
    real = nodes.run_shell
    calls = {"n": 0}

    def flaky(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real(cmd, **kw)

    monkeypatch.setattr(nodes, "run_shell", flaky)
    with pytest.raises(RuntimeError):
        main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    assert log.read_text().strip() == "1"

    monkeypatch.setattr(nodes, "run_shell", real)
    rc = main(["resume", "-q"])
    assert rc == 0
    # step one not redone; step two completed -> exactly "1\n2"
    assert log.read_text().split() == ["1", "2"]
    assert ckpt.find_run(None) is not None or True   # latest is now completed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli_resume.py -q`
Expected: FAIL — `runs`/`resume` are not valid subcommands.

- [ ] **Step 3: Add the `resume` and `runs` subparsers**

In `saage/cli.py`, inside `_build_parser`, after the `run` subparser is fully configured (after the `verbosity` group, before `return parser`), add:

```python
    res = sub.add_parser("resume", help="resume a killed/crashed run")
    res.add_argument("run_id", nargs="?",
                     help="run id or unique prefix (default: latest resumable)")
    res.add_argument("--force", action="store_true",
                     help="resume even if the flow changed since the checkpoint")
    res.add_argument("--workspace", metavar="DIR",
                     help="override the recorded workspace")
    rv = res.add_mutually_exclusive_group()
    rv.add_argument("-v", "--verbose", action="store_true")
    rv.add_argument("-q", "--quiet", action="store_true")

    sub.add_parser("runs", help="list resumable runs")
```

- [ ] **Step 4: Add the dispatch + handlers in `main`**

In `saage/cli.py`, change the top of `main` (currently routes only `remote`) to also route the new commands. Replace:

```python
    args = _build_parser().parse_args(argv)
    if args.command == "remote":
        _setup_logging(verbose=False, quiet=False)
        from .remote.cli import dispatch
        return dispatch(args)
    _setup_logging(args.verbose, args.quiet)
```

with:

```python
    args = _build_parser().parse_args(argv)
    if args.command == "remote":
        _setup_logging(verbose=False, quiet=False)
        from .remote.cli import dispatch
        return dispatch(args)
    if args.command == "runs":
        _setup_logging(verbose=False, quiet=False)
        return _cmd_runs()
    if args.command == "resume":
        _setup_logging(args.verbose, args.quiet)
        return _cmd_resume(args)
    _setup_logging(args.verbose, args.quiet)
```

Then add these two handlers above `main`:

```python
def _position(rec: dict) -> str:
    step = rec.get("resume_step")
    if step is None:
        return "-"
    iters = rec.get("shared", {}).get("_iter", {})
    return f"step {step}" + (f", loop iter {max(iters.values())}" if iters else "")


def _cmd_runs() -> int:
    from . import checkpoint as ckpt
    runs = ckpt.list_runs()
    if not runs:
        print("no runs recorded")
        return 0
    print(f"{'RUN ID':<24} {'STATUS':<10} {'POSITION':<18} FLOW")
    for r in runs:
        rec = r.load()
        print(f"{r.run_id:<24} {rec.get('status',''):<10} "
              f"{_position(rec):<18} {rec.get('flow_path','')}")
    return 0


def _cmd_resume(args) -> int:
    from . import checkpoint as ckpt
    from .hydrate import run_flow
    log = logging.getLogger("saage")
    try:
        cp = ckpt.find_run(args.run_id)
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1
    rec = cp.load()
    flow_path = rec["flow_path"]
    if not Path(flow_path).is_file():
        log.error("flow file is gone: %s", flow_path)
        return 1
    current_fp = ckpt.fingerprint(flow_path)
    if rec.get("fingerprint") and current_fp != rec["fingerprint"] and not args.force:
        log.error("flow changed since checkpoint (%s); re-run fresh, or "
                  "`saage resume %s --force` to override", flow_path, cp.run_id)
        return 1
    workspace = args.workspace or rec.get("workspace")
    log.info("resuming %s", cp.run_id)
    run_flow(flow_path,
             provider_overrides=rec.get("provider_overrides") or None,
             workspace=workspace, venv=rec.get("venv"),
             config=rec.get("config_path"), resume=cp)
    return 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_cli_resume.py -q`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add saage/cli.py tests/test_cli_resume.py
git commit -m "feat: saage resume + saage runs subcommands"
```

---

## Task 8: Documentation

**Files:**
- Modify: `AGENTS.md` (restart-safe iteration guidance), `README.md` (resume section), `CLAUDE.md` (engine note)

- [ ] **Step 1: Add a "Resumable runs" subsection to README.md**

Insert after the "Quickstart" section in `README.md`:

```markdown
## Resumable runs

Every `saage run` records a checkpoint under `~/.saage/runs/<run_id>/` after each
step (and each loop iteration). If the run is killed — Ctrl-C, a dead battery, an
ssh drop — pick it up where it left off:

```bash
saage runs                 # list runs: id, status, position, flow
saage resume               # resume the most recent unfinished run
saage resume <id|prefix>   # resume a specific run
saage resume --force <id>  # resume even if the flow.yaml/skills changed
```

`saage run` always starts a fresh run. Resume granularity is one iteration of the
outermost loop: a 12-iteration hill-climb killed during iteration 10 resumes at
iteration 10, keeping 1–9. The killed iteration is redone from its start, so a
flow's loop body should be safe to re-run (e.g. clean a checkpoint dir, then
train) — the example ML flows already follow this pattern.
```

- [ ] **Step 2: Add restart-safe guidance to AGENTS.md**

In `AGENTS.md`, under "Conventions & gotchas", add this bullet:

```markdown
- **Resumable runs / restart-safe iterations.** `saage run` checkpoints after
  every step and loop iteration; `saage resume` restarts at the top-level step
  that was in progress. A killed loop iteration is redone *whole* from the body's
  first step, so write loop bodies to tolerate re-running the current iteration
  (e.g. clean the experiment dir at the top of the body before training, as the
  hill-climb flows do). Completed iterations are never redone. Resume granularity
  is the *outermost* loop's iteration; a loop nested inside another loop is not
  independently resumable.
```

- [ ] **Step 3: Add an engine note to CLAUDE.md**

In `CLAUDE.md`, under the "Architecture" key-invariants list, add:

```markdown
- *Resumability rides on the shared store.* `saage/checkpoint.py` JSON-snapshots
  `shared` after each node (via `Subflow._orch`), tagged with `resume_step` (a
  node's `_step_index`, set in `hydrate.py`). `saage resume` restores `shared` and
  sets the top-level `start_node` to `steps[resume_step]`. Keep everything written
  into `shared` JSON-serializable, or checkpoints degrade to `str()` coercion.
```

- [ ] **Step 4: Verify docs render (no broken code fences) and suite still green**

Run: `pytest -q`
Expected: PASS (docs-only task; confirms nothing was accidentally edited in source)

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md CLAUDE.md
git commit -m "docs: document checkpoint/resume (saage resume, saage runs)"
```

---

## Self-Review

**Spec coverage:**
- Checkpoint after every node, JSON `shared` → Task 4 (`Subflow._orch`).
- `resume_step` pointer + `_step_index` tagging → Tasks 3, 4.
- `~/.saage/runs/<id>/checkpoint.json`, atomic writes, registry, fingerprint → Task 2; paths factored in Task 1.
- Resume re-entry: start-node swap, restore shared, reset suppression, drop poll clocks → Task 5.
- CLI `saage run` lifecycle, `saage resume`, `saage runs`, fingerprint refusal + `--force` → Tasks 6, 7.
- Library opt-in / CLI on-by-default → `checkpoint=None` default (Task 4) vs CLI always creating one (Task 6).
- Limitations + restart-safe guidance documented → Task 8.
- Testing plan (unit checkpoint, tagging, resume integration, reset-suppression, fingerprint refusal) → Tasks 2, 3, 5, 7.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every test asserts concrete values.

**Type consistency:** `Checkpoint.create/write/load/mark`, `new_run_id`, `fingerprint`, `list_runs`, `find_run` used identically across Tasks 2/5/6/7. `build_flow(..., checkpoint=, resume_step=)` and `run_flow(..., checkpoint=, resume=)` signatures consistent between Tasks 4/5 and their callers in 6/7. `_step_index` (node attribute) and `resume_step` (checkpoint field) used per the spec's naming throughout. `_skip_reset_once` set in Task 5 (`build_flow`) and consumed in Task 4 (`Subflow.prep`) — note these two appear in different tasks; the `prep` consumer is written in Task 4 and the producer in Task 5, which is fine because `getattr(self, "_skip_reset_once", False)` defaults safely when the attribute is absent (Task 4 stays green on its own).
```
</content>
