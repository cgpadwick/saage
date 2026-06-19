"""Laptop-side run state: ~/.saage/runs/<run_id>/.

    state.json     current snapshot — atomic tmp+rename writes, merge-updated
    events.jsonl   append-only audit trail (one JSON object per line, ts'd)
    manifest.json  what was handed off (flow, target, workspace ref, settings)
    handoff.log    output of the handoff itself (bootstrap output etc.)

State files record *intent*; the node records *truth*; `saage remote ps`
reconciles the two.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# `..` reaches the top-level `saage` package: paths.py lives there (parent of
# this `saage.remote` subpackage), shared with the engine's checkpoint store.
from ..paths import runs_dir


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunState:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.dir = runs_dir() / run_id

    @classmethod
    def create(cls, run_id: str) -> "RunState":
        rs = cls(run_id)
        rs.dir.mkdir(parents=True, exist_ok=True)
        return rs

    @property
    def exists(self) -> bool:
        return (self.dir / "state.json").exists()

    # -- state.json ----------------------------------------------------------

    def state(self) -> dict:
        try:
            return json.loads((self.dir / "state.json").read_text())
        except FileNotFoundError:
            return {}

    def update(self, **fields) -> dict:
        """Merge fields into state.json atomically (tmp + rename)."""
        state = self.state()
        state.update(fields)
        state["run_id"] = self.run_id
        state["updated_at"] = _now()
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        os.replace(tmp, self.dir / "state.json")
        return state

    # -- events.jsonl ----------------------------------------------------------

    def event(self, event: str, **fields) -> None:
        record = {"ts": _now(), "event": event, **fields}
        with open(self.dir / "events.jsonl", "a") as fh:
            fh.write(json.dumps(record) + "\n")

    def events(self) -> list[dict]:
        try:
            lines = (self.dir / "events.jsonl").read_text().splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    # -- manifest.json ---------------------------------------------------------

    def write_manifest(self, manifest: dict) -> None:
        (self.dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def manifest(self) -> dict:
        try:
            return json.loads((self.dir / "manifest.json").read_text())
        except FileNotFoundError:
            return {}


def list_runs() -> list[RunState]:
    base = runs_dir()
    if not base.exists():
        return []
    runs = [RunState(p.name) for p in sorted(base.iterdir()) if p.is_dir()]
    return [r for r in runs if r.exists]


def find_run(ref: str | None) -> RunState:
    """Resolve a run by id or unique prefix; with ref=None, the most recent run."""
    runs = list_runs()
    if not runs:
        raise FileNotFoundError("no runs recorded — nothing handed off yet?")
    if ref is None:
        return max(runs, key=lambda r: r.state().get("started_at", ""))
    matches = [r for r in runs if r.run_id == ref] or \
              [r for r in runs if r.run_id.startswith(ref)]
    if not matches:
        raise FileNotFoundError(f"no run matching {ref!r}")
    if len(matches) > 1:
        ids = ", ".join(r.run_id for r in matches)
        raise FileNotFoundError(f"ambiguous run {ref!r}: {ids}")
    return matches[0]
