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
        try:
            rel = f.relative_to(flow_dir).as_posix()
        except ValueError:
            rel = f.name
        h.update(rel.encode())
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


def _safe_load(cp: "Checkpoint") -> dict | None:
    try:
        return cp.load()
    except (json.JSONDecodeError, OSError) as e:
        log.warning("checkpoint: skipping unreadable run %s (%s)", cp.run_id, e)
        return None


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
        live = [(r, rec) for r in runs if (rec := _safe_load(r)) is not None]
        resumable = [(r, rec) for r, rec in live if rec.get("status") != "completed"]
        if not resumable:
            raise FileNotFoundError("no resumable runs (all completed)")
        return max(resumable, key=lambda rr: rr[1].get("started_at", ""))[0]
    matches = [r for r in runs if r.run_id == ref] or \
              [r for r in runs if r.run_id.startswith(ref)]
    if not matches:
        raise FileNotFoundError(f"no run matching {ref!r}")
    if len(matches) > 1:
        ids = ", ".join(r.run_id for r in matches)
        raise FileNotFoundError(f"ambiguous run {ref!r}: {ids}")
    return matches[0]
