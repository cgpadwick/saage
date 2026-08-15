"""Job registry: launch, track, and cancel saage run subprocesses.

Each launched run is recorded as a JSON line in
``saage_home()/"server"/"jobs.jsonl"``.  Appended lines for the same job_id
supersede earlier ones — last line per id wins on read.

Status resolution order:
1. ``cancelled`` flag in the registry entry
2. pid still alive → ``running``
3. ``checkpoint.json`` status (``running`` + dead pid → ``crashed``)
4. ``unknown``
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..checkpoint import new_run_id
from ..paths import saage_home
from .catalog import FlowInfo


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pid_is_ours(pid: int, run_id: str) -> bool | None:
    """Guard against PID reuse: check /proc/<pid>/cmdline for the run_id.

    Every process we launch carries ``--run-id <run_id>`` on its command line,
    so a live pid whose cmdline lacks the run_id is a recycled pid, not our
    child.  Returns True/False when the check is possible, or None when it
    isn't (no /proc — e.g. macOS — or unreadable), in which case callers fall
    back to signal-0 liveness alone.  Note: zombies have an empty cmdline and
    so report False, which is correct — the process is dead.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return False               # no such pid
    except OSError:
        return None                # /proc unavailable; can't tell
    return run_id.encode() in raw


@dataclass
class Job:
    job_id: str
    flow_name: str
    flow_path: str
    overrides: dict[str, str]
    pid: int
    created_at: str = field(default_factory=_now)


class JobRegistry:
    """Registry backed by an append-only ``jobs.jsonl`` file."""

    def __init__(self, home: Path | None = None):
        self._home = Path(home) if home else saage_home()
        self._registry_dir = self._home / "server"
        self._jobs_file = self._registry_dir / "jobs.jsonl"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_all(self) -> dict[str, dict]:
        """Return a dict of job_id → entry (last line per id wins)."""
        if not self._jobs_file.is_file():
            return {}
        entries: dict[str, dict] = {}
        for line in self._jobs_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                entries[rec["job_id"]] = rec
            except (json.JSONDecodeError, KeyError):
                continue
        return entries

    def _append(self, record: dict) -> None:
        self._registry_dir.mkdir(parents=True, exist_ok=True)
        with open(self._jobs_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def launch(self, flow: FlowInfo, overrides: dict[str, Any],
               workspace: str | None = None) -> Job:
        """Validate overrides, allocate a run_id, and spawn the child process."""
        for key in overrides:
            if key not in flow.knobs:
                raise ValueError(
                    f"unknown override {key!r} for flow {flow.name!r}; "
                    f"valid knobs: {sorted(flow.knobs)}")

        run_id = new_run_id()

        # Pre-create the run directory so the log file has a home.  The child
        # (saage run) will also call Checkpoint.create, which is idempotent.
        run_dir = self._home / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        log_path = run_dir / "server_launch.log"

        cmd = [sys.executable, "-m", "saage.cli", "run",
               str(flow.path), "--run-id", run_id]
        for k, v in overrides.items():
            cmd += ["--set", f"{k}={v}"]
        if workspace is not None:
            cmd += ["--workspace", workspace]

        env = {**os.environ, "SAAGE_HOME": str(self._home)}

        with open(log_path, "w", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(flow.path.parent),
                env=env,
            )

        job = Job(
            job_id=run_id,
            flow_name=flow.name,
            flow_path=str(flow.path),
            overrides=dict(overrides),
            pid=proc.pid,
        )
        self._append({**asdict(job), "cancelled": False})
        return job

    def list(self) -> list[dict]:
        """All registry entries, newest-first, each with a derived ``status``."""
        entries = self._read_all()
        result = []
        for entry in reversed(list(entries.values())):
            d = dict(entry)
            d["status"] = self.status(d["job_id"])
            result.append(d)
        return result

    def get(self, job_id: str) -> dict | None:
        entry = self._read_all().get(job_id)
        if entry is None:
            return None
        d = dict(entry)
        d["status"] = self.status(job_id)
        return d

    def status(self, job_id: str) -> str:
        """Derive the current status for a job."""
        entry = self._read_all().get(job_id)
        if entry is None:
            return "unknown"

        if entry.get("cancelled"):
            return "cancelled"

        pid = entry.get("pid")
        pid_alive = False
        if pid is not None:
            # Try non-blocking wait first so we reap zombies: a process that has
            # exited but not been waited on is still visible to os.kill(pid, 0),
            # causing status() to return "running" forever.
            try:
                result = os.waitpid(pid, os.WNOHANG)
                if result == (0, 0):
                    pid_alive = True   # still running
                # else: reaped the zombie; pid_alive stays False
            except ChildProcessError:
                # Not our child (e.g. after a server restart) — fall back to
                # signal-0 liveness check, guarded against pid reuse.
                try:
                    os.kill(pid, 0)
                    pid_alive = _pid_is_ours(pid, job_id) is not False
                except ProcessLookupError:
                    pid_alive = False
                except PermissionError:
                    # pid exists but owned by another user: can't be the child
                    # we spawned unless the cmdline check is unavailable.
                    pid_alive = _pid_is_ours(pid, job_id) is not False
                except OSError:
                    pid_alive = False
            except OSError:
                pid_alive = False

        if pid_alive:
            return "running"

        # pid dead — check checkpoint
        run_dir = self._home / "runs" / job_id
        cp_file = run_dir / "checkpoint.json"
        if cp_file.is_file():
            try:
                cp = json.loads(cp_file.read_text(encoding="utf-8"))
                cp_status = cp.get("status", "unknown")
                if cp_status == "running":
                    return "crashed"
                return cp_status
            except (json.JSONDecodeError, OSError):
                pass

        return "unknown"

    def cancel(self, job_id: str, grace: float = 5.0) -> bool:
        """Send SIGTERM to the process group; escalate to SIGKILL after grace.

        Returns True if the process was running and we signalled it;
        False if it was already gone.
        """
        entry = self._read_all().get(job_id)
        if entry is None:
            return False

        pid = entry.get("pid")
        if pid is None:
            return False

        # PID-reuse guard: never signal a pid that provably isn't our job's
        # process (recycled pid after a server restart, or an already-dead
        # child).  A dead/foreign pid just gets the cancelled flag.
        if _pid_is_ours(pid, job_id) is False:
            self._append({**entry, "cancelled": True})
            return False

        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            # already dead — mark cancelled anyway
            self._append({**entry, "cancelled": True})
            return False

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            self._append({**entry, "cancelled": True})
            return False

        deadline = time.monotonic() + grace
        reaped = False
        while time.monotonic() < deadline:
            # Non-blocking reap when we are the parent: detects zombie immediately.
            try:
                result = os.waitpid(pid, os.WNOHANG)
                if result != (0, 0):
                    reaped = True
                    break
            except ChildProcessError:
                # Not our child (e.g. after server restart); use signal-0 liveness check.
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break   # process is gone
                except PermissionError:
                    pass    # alive but owned by another user — keep waiting
                except OSError:
                    break
            time.sleep(0.1)
        else:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Blocking reap after SIGKILL if we haven't reaped yet.
            if not reaped:
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass

        self._append({**entry, "cancelled": True})
        return True
