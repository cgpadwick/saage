# saage server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `saage serve` — a localhost FastAPI job manager that discovers flows, launches them as subprocesses (GUI, curl, or LLM-parsed natural language with a confirm gate), and shows live logs + a live SVG DAG per run.

**Architecture:** Thin server over the existing run store (`~/.saage/runs/<id>/`: `checkpoint.json`, `ledger.jsonl`, `run.log`). Subprocess per job in its own process group; all run state is read from disk, never duplicated (only a small `jobs.jsonl` registry maps job → run_id/pid/flow). One additive engine change: ledger gains node-start events so the DAG can show "running".

**Tech Stack:** Python ≥3.10, FastAPI + uvicorn + jinja2 (new optional extra `server`), htmx (vendored static file), server-side SVG rendering (no JS graph lib), SSE for live updates. Spec: `docs/superpowers/specs/2026-08-15-saage-server-design.md`.

## Global Constraints

- Core engine dependencies unchanged; fastapi/uvicorn/sse deps live only in the `server` optional extra. `saage/server/` must import cleanly ONLY when the extra is installed; `saage serve` without it prints an install hint and exits 1.
- Server binds `127.0.0.1` only (default port 8321). No auth in v1.
- Deterministic control / LLM content: the parse endpoint's LLM output is strictly validated against the flow catalog; unknown flow or override key → rejected, never guessed. Nothing launches without an explicit `POST /api/jobs`.
- All tests offline: `ScriptedProvider` (exists in `saage/llm.py`) for LLM, tmp `SAAGE_HOME` (conftest already redirects it), stub flows in tmp dirs. No network, no real LLM.
- Run tests with `cd ~/code/saage && python -m pytest tests/ -q` (repo venv: `.venv` if present, else create one and `pip install -e .[dev,server]`).
- Every commit message ends with: `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`
- Follow existing repo conventions: stdlib logging via `log = logging.getLogger(__name__)`, docstrings explaining *why*, flake8-clean at 100 cols (repo uses ~100; match surrounding style).

## File Structure

```
saage/primitives.py          # MODIFY: ledger start events (phase field)
saage/cli.py                 # MODIFY: add `serve` subcommand
saage/server/__init__.py     # empty marker
saage/server/config.py       # ServerConfig: load ~/.saage/server.yaml
saage/server/catalog.py      # FlowCatalog: discover + validate flows, extract knobs
saage/server/dag.py          # spec → graph (nodes/edges/clusters); ledger → node states; SVG render
saage/server/jobs.py         # JobRegistry: jobs.jsonl, launch subprocess, status, cancel
saage/server/parse.py        # NL text + catalog → validated launch plan (LLM)
saage/server/app.py          # FastAPI app: API routes, SSE streams, page routes
saage/server/templates/      # base.html, home.html, job.html, history.html
saage/server/static/         # style.css, htmx.min.js (vendored)
tests/server/test_ledger_events.py
tests/server/test_catalog.py
tests/server/test_dag.py
tests/server/test_jobs.py
tests/server/test_parse.py
tests/server/test_api.py
pyproject.toml               # MODIFY: [server] extra, package-data for templates/static
README.md                    # MODIFY: server section
AGENTS.md                    # MODIFY: server subpackage note
```

Ordering: Task 1 (engine) is independent. Tasks 2–6 build the server bottom-up (config → catalog → dag → jobs → parse), each unit fully testable alone. Task 7 wires the API over them, Task 8 adds the UI, Task 9 docs.

---

### Task 1: Engine — node start/end ledger events

The DAG needs a "running" state; today `Subflow._ledger` appends only after a node completes. Add a best-effort start event before each node runs, and tag existing entries `phase: "end"`.

**Files:**
- Modify: `saage/primitives.py` (the `Subflow._orch` loop ~line 65, and `_ledger` ~line 100)
- Test: `tests/server/test_ledger_events.py` (create `tests/server/__init__.py` empty too)

**Interfaces:**
- Produces: `ledger.jsonl` lines gain `"phase": "start"|"end"`. Start lines: `{"step": int|None, "node": str, "phase": "start"}`. End lines: the existing shape (`step`, `node`, `action`, optional `exit`/`stdout_tail`/`output_tail`) plus `"phase": "end"`. Consumed by Task 3's state reducer.

- [ ] **Step 1: Write the failing test**

```python
"""Ledger start/end events: saage/server needs 'running' nodes, so _orch
appends a start line before each node runs and tags completion lines end."""
import json

from saage import checkpoint as ckpt
from saage.hydrate import run_flow


def _write_flow(tmp_path):
    (tmp_path / "flow.yaml").write_text(
        "provider: {type: local, model: m}\n"
        "workflow:\n"
        "  - {id: hello, type: command, run: 'echo hi'}\n"
        "  - {id: world, type: command, run: 'echo there'}\n")
    return tmp_path / "flow.yaml"


def test_ledger_has_start_and_end_phases(tmp_path):
    flow = _write_flow(tmp_path)
    run = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(flow))
    run_flow(flow, provider=object(), workspace=tmp_path, checkpoint=run)
    lines = [json.loads(x) for x in (run.dir / "ledger.jsonl").read_text().splitlines()]
    hello = [e for e in lines if e["node"] == "hello"]
    phases = [e.get("phase") for e in hello]
    assert "start" in phases and "end" in phases
    assert phases.index("start") < phases.index("end")
    start = next(e for e in hello if e.get("phase") == "start")
    assert "action" not in start          # start events carry no outcome fields
    end = next(e for e in hello if e.get("phase") == "end")
    assert end["exit"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_ledger_events.py -q`
Expected: FAIL — start phase missing (existing entries have no `phase` field).

- [ ] **Step 3: Implement**

In `Subflow._orch`, immediately before `last_action = curr._run(shared)`:

```python
            if self.sink is not None:
                self._ledger_start(curr)
            last_action = curr._run(shared)
```

Add to `Subflow` (next to `_ledger`), and add `"phase": "end"` to the entry dict inside the existing `_ledger`:

```python
    def _ledger_start(self, node) -> None:
        """Append a phase:start line before a node runs so live consumers
        (saage.server's DAG) can show 'running'. Best-effort like _ledger."""
        try:
            nid = getattr(node, "id", None) or type(node).__name__
            self.sink.append_ledger(
                {"step": getattr(node, "_step_index", None), "node": nid,
                 "phase": "start"})
        except Exception as e:                                # noqa: BLE001
            log.debug("ledger start append failed (non-fatal): %s", e)
```

Note: `_End`, `LoopGuard` etc. have no `id`; they'll log their class name — the DAG reducer (Task 3) ignores nodes it doesn't know. Skip noise cheaply: only append when `getattr(node, "id", None)` is truthy (apply the same guard in `_ledger_start`; leave `_ledger` end-event behavior unchanged for compatibility).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/server/test_ledger_events.py tests/test_checkpoint.py tests/test_primitives.py -q` then the full suite `python -m pytest tests/ -q`.
Expected: PASS (existing ledger consumers only read fields they know).

- [ ] **Step 5: Commit** — `feat(engine): ledger start events for live node status`

---

### Task 2: Server config + packaging + `saage serve` stub

**Files:**
- Create: `saage/server/__init__.py` (empty), `saage/server/config.py`
- Modify: `pyproject.toml` (extra + package data), `saage/cli.py`
- Test: `tests/server/test_catalog.py` will cover config loading (this task only needs a small direct test — put it in `tests/server/test_catalog.py` as its first test class)

**Interfaces:**
- Produces: `ServerConfig` dataclass: `flow_paths: list[Path]`, `parser_provider: dict | None`, `host: str = "127.0.0.1"`, `port: int = 8321`; `load_server_config(path: Path | None = None) -> ServerConfig` (default path `saage_home() / "server.yaml"`; missing file → defaults with `flow_paths=[]`).

- [ ] **Step 1: Write the failing test** (in `tests/server/test_catalog.py`)

```python
from saage.server.config import load_server_config


class TestServerConfig:
    def test_missing_file_yields_defaults(self, tmp_path):
        cfg = load_server_config(tmp_path / "nope.yaml")
        assert cfg.flow_paths == [] and cfg.port == 8321 and cfg.host == "127.0.0.1"

    def test_loads_and_expands_paths(self, tmp_path):
        (tmp_path / "server.yaml").write_text(
            "flow_paths: ['~/flows_a', 'rel_b']\n"
            "port: 9000\n"
            "parser_provider: {type: local, model: m}\n")
        cfg = load_server_config(tmp_path / "server.yaml")
        assert cfg.flow_paths[0].is_absolute()          # ~ expanded
        assert cfg.port == 9000
        assert cfg.parser_provider == {"type": "local", "model": "m"}
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/server/test_catalog.py -q` → import error.

- [ ] **Step 3: Implement `saage/server/config.py`**

```python
"""Server-side config: ~/.saage/server.yaml (flow search paths, parser LLM,
bind address). Kept separate from engine config — the engine never reads this."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..paths import saage_home


@dataclass
class ServerConfig:
    flow_paths: list = field(default_factory=list)
    parser_provider: dict | None = None
    host: str = "127.0.0.1"
    port: int = 8321


def load_server_config(path: Path | None = None) -> ServerConfig:
    p = Path(path) if path else saage_home() / "server.yaml"
    if not p.is_file():
        return ServerConfig()
    raw = yaml.safe_load(p.read_text()) or {}
    return ServerConfig(
        flow_paths=[Path(x).expanduser().resolve() for x in raw.get("flow_paths", [])],
        parser_provider=raw.get("parser_provider"),
        host=raw.get("host", "127.0.0.1"),
        port=int(raw.get("port", 8321)))
```

- [ ] **Step 4: pyproject + CLI.** In `pyproject.toml` add to `[project.optional-dependencies]`:

```toml
server = ["fastapi", "uvicorn", "jinja2"]
```

and (templates/static shipping — add now so it's not forgotten):

```toml
[tool.setuptools.package-data]
"saage.server" = ["templates/*.html", "static/*"]
```

In `saage/cli.py` `_build_parser()` add after the `runs` subparser:

```python
    srv = sub.add_parser("serve", help="run the local flow job-manager web UI")
    srv.add_argument("--host", default=None, help="override server.yaml host")
    srv.add_argument("--port", type=int, default=None, help="override server.yaml port")
    srv.add_argument("--config", default=None, help="path to server.yaml")
```

and in `main()` next to the `resume` dispatch:

```python
    if args.command == "serve":
        try:
            from .server.app import serve
        except ImportError as e:
            log.error("saage serve needs the server extra: pip install saage[server] (%s)", e)
            return 1
        return serve(config_path=args.config, host=args.host, port=args.port)
```

(`serve()` arrives in Task 7; until then `saage serve` fails the import cleanly — acceptable mid-plan state.)

- [ ] **Step 5: Run tests + full suite; commit** — `feat(server): server.yaml config, extra, serve subcommand`

---

### Task 3: Flow catalog (discovery + knobs)

**Files:**
- Create: `saage/server/catalog.py`
- Test: `tests/server/test_catalog.py` (append)

**Interfaces:**
- Consumes: `ServerConfig.flow_paths`; `saage.hydrate.build_flow` for validation.
- Produces:
  - `FlowInfo` dataclass: `name: str`, `path: Path` (the flow.yaml), `description: str`, `knobs: dict[str, str]` (the YAML `shared:` block, values stringified), `spec: dict` (parsed YAML), `error: str | None` (None if it hydrated).
  - `FlowCatalog(config).refresh() -> None`, `.flows -> dict[str, FlowInfo]` (name → info; name collisions across paths: first path wins, later logged), `.get(name) -> FlowInfo | None`.

- [ ] **Step 1: Write the failing tests**

```python
from saage.server.catalog import FlowCatalog
from saage.server.config import ServerConfig

GOOD = ("# A demo flow.\n# Second line ignored.\n"
        "provider: {type: local, model: m}\n"
        "shared: {knob_a: '1', knob_b: hello}\n"
        "workflow:\n  - {id: s1, type: command, run: 'echo hi'}\n")
BROKEN = "provider: {type: local, model: m}\nworkflow:\n  - {id: s1, type: nope}\n"


def _mk(tmp_path, name, text):
    d = tmp_path / "flows" / name
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(text)


class TestCatalog:
    def test_discovers_and_extracts_knobs(self, tmp_path):
        _mk(tmp_path, "demo", GOOD)
        cat = FlowCatalog(ServerConfig(flow_paths=[tmp_path / "flows"]))
        cat.refresh()
        info = cat.get("demo")
        assert info.error is None
        assert info.knobs == {"knob_a": "1", "knob_b": "hello"}
        assert info.description == "A demo flow."

    def test_broken_flow_listed_with_error(self, tmp_path):
        _mk(tmp_path, "bad", BROKEN)
        cat = FlowCatalog(ServerConfig(flow_paths=[tmp_path / "flows"]))
        cat.refresh()
        assert cat.get("bad").error          # listed, not hidden
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `saage/server/catalog.py`**

```python
"""Flow discovery: scan configured dirs for */flow.yaml, hydrate each for free
validation, and cache name/description/knobs/spec for the API, the NL parser's
prompt, and the DAG builder. Broken flows are listed with their error — an
invisible flow is a debugging trap."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..hydrate import build_flow

log = logging.getLogger(__name__)


@dataclass
class FlowInfo:
    name: str
    path: Path
    description: str
    knobs: dict
    spec: dict
    error: str | None = None


def _description(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()
        if line.strip():
            return ""
    return ""


class FlowCatalog:
    def __init__(self, config):
        self.config = config
        self.flows: dict[str, FlowInfo] = {}

    def refresh(self) -> None:
        found: dict[str, FlowInfo] = {}
        for base in self.config.flow_paths:
            for fy in sorted(Path(base).glob("*/flow.yaml")):
                name = fy.parent.name
                if name in found:
                    log.warning("catalog: duplicate flow %r at %s ignored", name, fy)
                    continue
                found[name] = self._load(name, fy)
        self.flows = found

    def get(self, name: str):
        return self.flows.get(name)

    def _load(self, name: str, fy: Path) -> FlowInfo:
        text = fy.read_text()
        spec = yaml.safe_load(text) or {}
        knobs = {k: str(v) for k, v in (spec.get("shared") or {}).items()}
        info = FlowInfo(name, fy, _description(text), knobs, spec)
        try:
            # hydrate against a throwaway workspace: free schema validation
            build_flow(fy, provider=object(), workspace=fy.parent)
        except Exception as e:                                # noqa: BLE001
            info.error = str(e)
        return info
```

(If `build_flow` requires a provider dict/None differently, adapt: the lof-agent-runner tests call `build_flow(path, provider=object(), workspace=...)` successfully — mirror that.)

- [ ] **Step 4: Run tests + full suite; commit** — `feat(server): flow catalog discovery`

---

### Task 4: DAG builder + ledger state reducer + SVG

**Files:**
- Create: `saage/server/dag.py`
- Test: `tests/server/test_dag.py`

**Interfaces:**
- Consumes: `FlowInfo.spec` (parsed flow YAML), ledger event dicts (Task 1 shape).
- Produces:
  - `build_graph(spec: dict) -> Graph` where `Graph` = dataclass with `nodes: list[GNode]` (`GNode`: `id, type, label, params: dict, cluster: str | None`), `edges: list[tuple[str, str]]`, `clusters: list[Cluster]` (`Cluster`: `id, kind` — `retry_loop|counting_loop|polling_loop` — `label, max_iterations: str`).
  - `reduce_states(events: list[dict]) -> dict[str, dict]`: node id → `{"state": "pending|running|done|failed", "attempts": int, "last": dict}`. Rules: `phase:start` → running; `phase:end` (or legacy no-phase end line) → done unless `action == "fail"` or nonzero `exit` → failed (a later start on the same node flips failed→running and bumps attempts).
  - `render_svg(graph: Graph, states: dict) -> str`: standalone `<svg>`; each node element `id="node-<id>"` with class `state-<state>`; simple vertical layered layout (nodes 180×44, 24px gaps; cluster members boxed with a labeled rect). Retry/polling loops draw action→check plus a dashed back-edge.

- [ ] **Step 1: Write the failing tests**

```python
import yaml

from saage.server.dag import build_graph, reduce_states, render_svg

FLOW = yaml.safe_load("""
provider: {type: local, model: m}
workflow:
  - {id: a, type: command, run: 'echo hi'}
  - id: fixit
    type: retry_loop
    max_iterations: 3
    action: {id: fix, type: agent, skill: s1}
    check: {id: verify, type: command, run: 'pytest -q'}
  - {id: z, type: command, run: 'echo done'}
""")


def test_graph_walks_loops_into_clusters():
    g = build_graph(FLOW)
    ids = [n.id for n in g.nodes]
    assert ids == ["a", "fix", "verify", "z"]
    assert ("a", "fix") in g.edges and ("verify", "z") in g.edges
    assert ("fix", "verify") in g.edges
    cl = next(c for c in g.clusters if c.id == "fixit")
    assert cl.kind == "retry_loop" and cl.max_iterations == "3"
    assert next(n for n in g.nodes if n.id == "fix").cluster == "fixit"


def test_reducer_tracks_running_done_failed_attempts():
    ev = [{"node": "a", "phase": "start"},
          {"node": "a", "phase": "end", "action": "default", "exit": 0},
          {"node": "fix", "phase": "start"},
          {"node": "fix", "phase": "end", "action": "default"},
          {"node": "verify", "phase": "start"},
          {"node": "verify", "phase": "end", "action": "fail", "exit": 1},
          {"node": "fix", "phase": "start"}]
    s = reduce_states(ev)
    assert s["a"]["state"] == "done"
    assert s["verify"]["state"] == "failed"
    assert s["fix"]["state"] == "running" and s["fix"]["attempts"] == 2


def test_svg_marks_states():
    g = build_graph(FLOW)
    svg = render_svg(g, {"a": {"state": "done", "attempts": 1, "last": {}}})
    assert svg.startswith("<svg") and 'id="node-a"' in svg and "state-done" in svg
    assert "state-pending" in svg          # nodes without events default pending
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Walk the workflow recursing into `body` (counting_loop) and `action`/`check` / `poll`/`classify`-style keys — mirror the recursion the engine's own hydrate uses: for each step dict, if `type` in `{retry_loop, polling_loop, counting_loop}` create a `Cluster` and recurse into its child keys (`action`, `check`, `poll`, `status`, `body`), assigning `cluster=<loop id>` to leaf nodes; chain consecutive leaves with edges in document order (the engine executes them as a linear chain; loops add the dashed back-edge from last→first member). Node `params`: for commands `{"run": spec["run"], "set": spec.get("set", {})}`; for agents `{"skill": spec["skill"], "set": ..., "max_steps": ...}`. `render_svg` computes `y = 70 * index`, wraps cluster members in a `<rect class="cluster">` sized to cover them, and emits per node:

```python
f'<g id="node-{n.id}" class="dagnode state-{state}" data-node="{n.id}">'
f'<rect x="{x}" y="{y}" width="180" height="44" rx="8"/>'
f'<text x="{x+90}" y="{y+22}">{n.label}</text>'
f'<text x="{x+90}" y="{y+36}" class="sub">{n.type}{attempts_badge}</text></g>'
```

Edges as `<line>`/`<path>` between node centers; back-edges `class="backedge"` (dashed via CSS). Keep it a single ~150-line module; no external layout lib — saage graphs are chains with non-overlapping clusters.

- [ ] **Step 4: Run tests + full suite; commit** — `feat(server): DAG builder, ledger reducer, SVG renderer`

---

### Task 5: Job registry + subprocess launcher

**Files:**
- Create: `saage/server/jobs.py`
- Test: `tests/server/test_jobs.py`

**Interfaces:**
- Consumes: `saage.paths.saage_home()`, `saage.checkpoint` (run dirs), `FlowInfo`.
- Produces `JobRegistry(home: Path | None = None)`:
  - `.launch(flow: FlowInfo, overrides: dict, workspace: str | None = None) -> Job` — validates every override key against `flow.knobs` (unknown → `ValueError`), allocates `run_id = checkpoint.new_run_id()`, spawns `[sys.executable, "-m", "saage.cli", "run", str(flow.path), "--run-id", run_id] + ["--set", f"{k}={v}" ...]` (+ `--workspace` if given) with `start_new_session=True`, stdout/stderr → `<run_dir>/server_launch.log` (run dir pre-created via `Checkpoint.create` is NOT done here — the child creates it; the launcher creates only the log file's parent via `runs_dir()/run_id` mkdir), env passthrough plus `SAAGE_HOME` preserved.
  - `Job` dataclass: `job_id (== run_id), flow_name, flow_path, overrides, pid, created_at`.
  - `.list() -> list[dict]` — registry entries newest-first, each with derived `status`.
  - `.get(job_id) -> dict | None`; `.status(job_id) -> str`: `running` if pid alive (`os.kill(pid, 0)`), else `cancelled` if the registry marked it, else `checkpoint.json`'s `status` (`completed|failed|running`→`crashed` if pid dead but checkpoint says running), else `unknown`.
  - `.cancel(job_id, grace: float = 5.0) -> bool` — `os.killpg(pid, SIGTERM)`, poll up to `grace` seconds, then `SIGKILL`; marks entry `cancelled: true` in `jobs.jsonl` (append a superseding line; last line per job wins on read).
  - Registry file: `saage_home()/"server"/"jobs.jsonl"`, append-only JSON lines.

- [ ] **Step 1: Write the failing tests** (stub "flow": a real flow.yaml whose single command step is `sleep 30`, so the child is `saage run` for real — slow to finish but instant to launch/cancel; use the local provider type so no key is needed and the flow has no agent steps)

```python
import json
import os
import time
from pathlib import Path

import pytest

from saage.server.catalog import FlowCatalog
from saage.server.config import ServerConfig
from saage.server.jobs import JobRegistry

SLEEPER = ("provider: {type: local, model: m}\n"
           "shared: {seconds: '30'}\n"
           "workflow:\n  - {id: nap, type: command, run: 'sleep {{ seconds }}'}\n")


@pytest.fixture
def flow_info(tmp_path):
    d = tmp_path / "flows" / "sleeper"
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(SLEEPER)
    cat = FlowCatalog(ServerConfig(flow_paths=[tmp_path / "flows"]))
    cat.refresh()
    return cat.get("sleeper")


def test_unknown_override_rejected(flow_info):
    reg = JobRegistry()
    with pytest.raises(ValueError, match="nonsense"):
        reg.launch(flow_info, {"nonsense": "1"})


def test_launch_status_cancel_roundtrip(flow_info):
    reg = JobRegistry()
    job = reg.launch(flow_info, {"seconds": "30"})
    assert reg.status(job.job_id) == "running"
    assert reg.get(job.job_id)["overrides"] == {"seconds": "30"}
    assert reg.cancel(job.job_id)
    deadline = time.time() + 10
    while time.time() < deadline and reg.status(job.job_id) == "running":
        time.sleep(0.2)
    assert reg.status(job.job_id) == "cancelled"
    with pytest.raises(OSError):
        os.kill(job.pid, 0)               # process group is gone


def test_registry_survives_restart(flow_info):
    job = JobRegistry().launch(flow_info, {})
    reg2 = JobRegistry()                   # fresh instance, same SAAGE_HOME
    assert reg2.get(job.job_id)["flow_name"] == "sleeper"
    reg2.cancel(job.job_id)
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Key details: `subprocess.Popen(cmd, start_new_session=True, stdout=log_fh, stderr=subprocess.STDOUT, cwd=str(flow.path.parent))`; jobs.jsonl read = parse all lines, `dict` keyed by job_id keeps the last line (append-only updates); `status()` order: registry `cancelled` flag → pid alive? running → checkpoint.json status (`running` there + dead pid = `crashed`) → `unknown`. `cancel()` uses `os.killpg(os.getpgid(pid), ...)` guarded for `ProcessLookupError` (already dead → still mark cancelled=False if never running... keep it simple: return False if the process was already gone).

- [ ] **Step 4: Run tests + full suite; commit** — `feat(server): job registry and subprocess launcher`

---

### Task 6: Natural-language parse (LLM, validated)

**Files:**
- Create: `saage/server/parse.py`
- Test: `tests/server/test_parse.py`

**Interfaces:**
- Consumes: `FlowCatalog`, an `LLMProvider` (anything with `.complete(system, messages, tools)` → object with `.text`; use `saage.llm.ScriptedProvider` in tests; the app builds the real one via `saage.hydrate.make_provider(config.parser_provider)`).
- Produces: `parse_launch(text: str, catalog: FlowCatalog, provider) -> dict` returning `{"ok": True, "flow": str, "overrides": dict, "explanation": str}` or `{"ok": False, "error": str}`. Never raises on bad LLM output.

- [ ] **Step 1: Write the failing tests**

```python
from saage.llm import ScriptedProvider
from saage.server.parse import parse_launch

# reuse the catalog fixture pattern from test_catalog (GOOD flow with knob_a/knob_b)


def test_valid_plan_passes(catalog):
    p = ScriptedProvider(['{"flow": "demo", "overrides": {"knob_a": "5"}, '
                          '"explanation": "sets knob_a"}'])
    out = parse_launch("run demo with knob_a five", catalog, p)
    assert out == {"ok": True, "flow": "demo", "overrides": {"knob_a": "5"},
                   "explanation": "sets knob_a"}


def test_unknown_flow_rejected(catalog):
    p = ScriptedProvider(['{"flow": "ghost", "overrides": {}, "explanation": "x"}'])
    out = parse_launch("run ghost", catalog, p)
    assert not out["ok"] and "ghost" in out["error"]


def test_unknown_knob_rejected(catalog):
    p = ScriptedProvider(['{"flow": "demo", "overrides": {"batch": "2"}, '
                          '"explanation": "x"}'])
    out = parse_launch("sweep batch", catalog, p)
    assert not out["ok"] and "batch" in out["error"]


def test_non_json_rejected(catalog):
    out = parse_launch("hi", catalog, ScriptedProvider(["sure! I will run demo"]))
    assert not out["ok"] and "JSON" in out["error"]
```

Check `ScriptedProvider`'s constructor/reply shape in `saage/llm.py` first and adapt the test to its real API (it exists at line ~273; replies come back as the provider's response object — extract `.text` accordingly).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** System prompt = fixed instructions + the catalog serialized as JSON (`name`, `description`, `knobs` with defaults): *"Map the user's request to exactly one flow and overrides for existing knobs only. Reply with strict JSON {flow, overrides, explanation} and nothing else. Override values are strings. If the request doesn't match any flow or asks for knobs that don't exist, reply {\"error\": \"<why>\"}."* Parse the reply: strip Markdown fences if present, `json.loads` (failure → `{"ok": False, "error": "model reply was not valid JSON: ..."}`), then validate: flow exists in catalog and isn't broken (`error is None`), every override key ∈ `flow.knobs`. Stringify override values. The user text goes in the user message — data, not part of the system prompt.

- [ ] **Step 4: Run tests + full suite; commit** — `feat(server): validated natural-language launch parsing`

---

### Task 7: FastAPI app — API routes + SSE

**Files:**
- Create: `saage/server/app.py`
- Test: `tests/server/test_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `create_app(config: ServerConfig, provider=None) -> FastAPI` (provider injectable for tests; default built lazily from `config.parser_provider` on first `/api/parse`, 503 if unconfigured).
  - `serve(config_path=None, host=None, port=None) -> int` — loads config, `uvicorn.run(create_app(cfg), host=..., port=...)`; returns 0. (This completes the Task 2 CLI stub.)
  - Routes exactly as the spec table: `GET /api/flows`, `POST /api/parse` `{text}`, `POST /api/jobs` `{flow, overrides, workspace?}` (404 unknown flow, 422 unknown knob → error body), `GET /api/jobs`, `GET /api/jobs/{id}` (includes `shared` snapshot from checkpoint.json), `GET /api/jobs/{id}/logs` (SSE; `?since=<byte offset>`), `GET /api/jobs/{id}/ledger` (SSE; replays file then follows), `POST /api/jobs/{id}/cancel`, `POST /api/flows/refresh`.
  - SSE framing: `data: <json>\n\n` per event; log stream sends `{"chunk": str, "offset": int}`; ledger stream sends each ledger line's dict; both send `event: done\ndata: {"status": <final>}\n\n` and close when the job reaches a terminal status. Tail loop: poll file growth every 0.5s via a thread-safe generator (StreamingResponse with `media_type="text/event-stream"`).

- [ ] **Step 1: Write the failing tests** (reuse the sleeper-flow fixture from test_jobs; TestClient from fastapi)

```python
from fastapi.testclient import TestClient

from saage.llm import ScriptedProvider
from saage.server.app import create_app
from saage.server.config import ServerConfig


def _client(tmp_path, replies=()):
    cfg = ServerConfig(flow_paths=[tmp_path / "flows"])
    return TestClient(create_app(cfg, provider=ScriptedProvider(list(replies))))


def test_flows_endpoint_lists_catalog(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    flows = c.get("/api/flows").json()
    assert flows[0]["name"] == "sleeper" and "seconds" in flows[0]["knobs"]


def test_launch_status_cancel(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"flow": "sleeper", "overrides": {"seconds": "30"}})
    assert r.status_code == 201
    jid = r.json()["job_id"]
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "running"
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200


def test_launch_rejects_unknown_knob(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"flow": "sleeper", "overrides": {"nope": "1"}})
    assert r.status_code == 422 and "nope" in r.json()["detail"]


def test_parse_endpoint_round_trip(tmp_path, sleeper_flow):
    c = _client(tmp_path, ['{"flow": "sleeper", "overrides": {"seconds": "5"},'
                           ' "explanation": "short nap"}'])
    out = c.post("/api/parse", json={"text": "nap for five seconds"}).json()
    assert out["ok"] and out["flow"] == "sleeper"


def test_logs_sse_streams_and_finishes(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "1"}}).json()["job_id"]
    with c.stream("GET", f"/api/jobs/{jid}/logs") as r:
        body = "".join(r.iter_text())
    assert "event: done" in body
```

Put the `sleeper_flow` fixture in a new `tests/server/conftest.py` (move it out of test_jobs so both files share it).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `app.py`.** Module-level guard so the import error is friendly:

```python
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse, HTMLResponse
except ImportError as e:      # pragma: no cover
    raise ImportError("saage.server requires: pip install saage[server]") from e
```

App state: `app.state.catalog` (refreshed at startup), `app.state.registry`, `app.state.provider` (lazy). Keep route handlers thin — all logic already lives in catalog/jobs/parse/dag. SSE generator sketch:

```python
def _tail(path, job_status, since=0):
    offset = since
    while True:
        if path.exists():
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read()
            if chunk:
                offset += len(chunk)
                yield f"data: {json.dumps({'chunk': chunk.decode(errors='replace'), 'offset': offset})}\n\n"
        s = job_status()
        if s not in ("running",):
            yield f"event: done\ndata: {json.dumps({'status': s})}\n\n"
            return
        time.sleep(0.5)
```

- [ ] **Step 4: Run tests + full suite; commit** — `feat(server): FastAPI app with jobs API and SSE streams`

---

### Task 8: UI — templates, static, page routes

**Files:**
- Create: `saage/server/templates/{base,home,job,history}.html`, `saage/server/static/style.css`
- Create: `saage/server/static/htmx.min.js` — vendor it: `curl -L https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js -o saage/server/static/htmx.min.js`
- Modify: `saage/server/app.py` (page routes + StaticFiles mount + Jinja2Templates)
- Test: `tests/server/test_api.py` (append page smoke tests)

**Interfaces:**
- Consumes: all API pieces; `dag.build_graph/reduce_states/render_svg`.
- Produces: `GET /` (home), `GET /jobs/{id}` (detail), `GET /history`; `GET /jobs/{id}/dag.svg` returning the current SVG (the page's inline JS re-fetches it on each ledger SSE event — simplest correct liveness; per-node class swapping is a later optimization).

- [ ] **Step 1: Write the failing smoke tests**

```python
def test_pages_render(tmp_path, sleeper_flow):
    c = _client(tmp_path)
    assert "sleeper" in c.get("/").text                  # dropdown + knob form
    jid = c.post("/api/jobs", json={"flow": "sleeper",
                                    "overrides": {"seconds": "1"}}).json()["job_id"]
    page = c.get(f"/jobs/{jid}").text
    assert "dag.svg" in page and "EventSource" in page
    svg = c.get(f"/jobs/{jid}/dag.svg")
    assert svg.headers["content-type"].startswith("image/svg")
    assert 'id="node-nap"' in svg.text
    assert c.get("/history").status_code == 200
    c.post(f"/api/jobs/{jid}/cancel")
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** `base.html`: dark-neutral single CSS file, header nav (Home / History). `home.html`: flow `<select>` (htmx `hx-get` swaps the knob form per flow), knob form posts to `/api/jobs` via htmx, NL `<textarea>` posts to a small `/parse-form` HTML endpoint that renders the plan with Confirm (htmx post to `/api/jobs`) / Edit (populates the form) / Cancel; active-jobs table with htmx 5s poll and cancel buttons. `job.html`: `<img>`-free inline `<object>`/fetched SVG in a div + `<pre id="log">`; inline script:

```html
<script>
  const log = document.getElementById("log");
  new EventSource("/api/jobs/{{ job_id }}/logs").onmessage = e => {
    log.textContent += JSON.parse(e.data).chunk; log.scrollTop = log.scrollHeight; };
  const dag = document.getElementById("dag");
  const es = new EventSource("/api/jobs/{{ job_id }}/ledger");
  const redraw = () => fetch("/jobs/{{ job_id }}/dag.svg").then(r => r.text())
                        .then(t => dag.innerHTML = t);
  es.onmessage = redraw; es.addEventListener("done", () => es.close()); redraw();
</script>
```

Node click → params panel: each SVG `<g>` carries `data-node`; a delegated click handler shows `graph.nodes[i].params` (serialize the graph to a JSON `<script type="application/json">` block in the page). `history.html`: table over `checkpoint.list_runs()` merged with the registry.

- [ ] **Step 4: Run tests + full suite, then a REAL manual smoke:** `pip install -e .[server]` in the repo venv, `SAAGE_HOME=/tmp/saage_demo saage serve --config <tmp server.yaml pointing at flows/>`, launch `story_writer` or a sleeper from the browser, watch the DAG light up, cancel it. Fix what the smoke reveals.

- [ ] **Step 5: Commit** — `feat(server): htmx UI — launch, live DAG, logs, history`

---

### Task 9: Docs

**Files:**
- Modify: `README.md` (new "saage serve" section: what it is, install extra, server.yaml example, screenshot placeholder omitted — text only), `AGENTS.md` (repo-map row for `saage/server/`, one paragraph: thin-over-run-store principle, ledger `phase` events, how to run server tests)

**Steps:**
- [ ] **Step 1:** Write both doc updates (README: quickstart = `pip install -e .[server]`, `cat > ~/.saage/server.yaml`, `saage serve`, open `http://127.0.0.1:8321`; NL launch example with the confirm gate; the curl equivalents for `/api/flows`, `/api/parse`, `/api/jobs`).
- [ ] **Step 2:** Full suite one final time: `python -m pytest tests/ -q`; flake8 if configured.
- [ ] **Step 3: Commit** — `docs: saage serve section + AGENTS.md server notes`

---

## Self-review notes

- Spec coverage: architecture/discovery (T2–T3), API+parse+confirm (T6–T7), UI+DAG (T4, T8), engine changes (T1; `--run-id` already exists so spec item 2 is a no-op), error handling (launch failures → `server_launch.log` + status derivation T5; parse errors T6; restart re-attach T5; SSE done events T7), testing strategy (mirrored per task), out-of-scope respected (no auth/Slack/queueing).
- Types consistent: `FlowInfo` (T3) consumed by T5/T6/T7; ledger event shape (T1) consumed by T4; `Graph/GNode/Cluster` (T4) consumed by T8; `ServerConfig` (T2) consumed everywhere.
- Known adaptation points called out inline: `ScriptedProvider`'s exact constructor, `build_flow` provider arg — implementer verifies against source rather than trusting the plan.
