"""Host mode: the calling coding agent IS the LLM.

`provider: { type: host }` (or `--provider host`) runs a flow with no API key.
The engine still owns all control flow — ordering, loop bounds, exit_when,
command steps — but each agent step is handed, whole, to whatever agent is
driving the run (Claude Code, Cursor, …) through a file mailbox in the run
dir. The host does the step with its own model and tools and hands back the
final text; captures/routing then proceed exactly as for any provider.

Mailbox protocol (`<run_dir>/host/`), one step outstanding at a time:
    engine → heartbeat           touched every HEARTBEAT_EVERY s while the engine lives
    engine → pending.json        {seq, step_id, skill, system, task, tools, workspace}
    host   → result-<seq>.json   {seq, output}
Writes are atomic (unique tmp + rename/link), so no side reads a torn file,
and a result is created exclusively, so of two racing submits exactly one wins.

Liveness comes from the run itself — the heartbeat, then the run's checkpoint
status — never from whoever launched it, so a run started by `saage run`,
`saage host start`, MCP, the web UI, or resumed with `saage resume` is
drivable the same way. The engine's wait is bounded by `timeout` (a vanished
host must not hang a run forever).

Host mode is local-only: the host's tools act on the host's filesystem, so a
remote handoff (whose workspace lives on another box) refuses it.
"""
from __future__ import annotations

import atexit
import json
import logging
import math
import os
import tempfile
import threading
import time
from pathlib import Path

from .llm import LLMResponse

log = logging.getLogger(__name__)

MAILBOX_ENV = "SAAGE_HOST_MAILBOX"   # set by `saage run` to mailbox_dir(run_dir)
DEFAULT_TIMEOUT = 3600.0             # max seconds to wait for one step's result
HEARTBEAT_EVERY = 0.5                # engine touches <mailbox>/heartbeat this often
STALE_AFTER = 5.0                    # …and is presumed dead this long after the last
                                     # (the window in which a SIGKILLed run still
                                     # looks alive; a clean exit removes the beat)
STARTUP_GRACE = 60.0                 # a run that never beat yet may still be starting
_POLL = 0.2


class HostTimeout(RuntimeError):
    """The host never answered a posted step within the timeout."""


def mailbox_dir(run_dir: "str | Path") -> Path:
    """The one place a run's mailbox lives."""
    return Path(run_dir) / "host"


def validated_timeout(value) -> float:
    """`provider.timeout` must be a finite, positive number of seconds — NaN/inf
    would make the wait unbounded (loops must terminate), <= 0 fails every step."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value <= 0:
        raise ValueError(f"provider 'host': timeout must be a positive number of "
                         f"seconds, got {value!r}")
    return float(value)


def _retry(fn, attempts: int = 20):
    """Windows: replacing/unlinking a file another process has open (the host
    polls pending.json) raises PermissionError for a moment — retry briefly
    instead of failing the run over a read that's already finishing."""
    for i in range(attempts):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05)


def _write_tmp(dirpath: Path, obj: dict) -> str:
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return tmp


def _write_json(path: Path, obj: dict) -> None:
    tmp = _write_tmp(path.parent, obj)
    _retry(lambda: os.replace(tmp, path))


def _unlink(path: Path) -> None:
    _retry(lambda: path.unlink(missing_ok=True))


# --------------------------------------------------------------------------- #
# Run status — shared by every host surface
# --------------------------------------------------------------------------- #
def _age(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return None


def run_status(run_dir: "str | Path") -> str:
    """running | completed | failed | crashed | unknown, for a hosted run.

    A fresh heartbeat means the engine is alive (this beats the checkpoint,
    which still says `failed` early in a resumed run). Without one, the
    checkpoint decides; `running` there with no live engine is a crash. A
    run that never beat yet is given STARTUP_GRACE to start up. The beat is
    checked first, so a live run's checkpoint is never even opened by a host
    (no read/replace collisions with the engine's checkpoint writes)."""
    run_dir = Path(run_dir)
    beat = _age(mailbox_dir(run_dir) / "heartbeat")
    if beat is not None and beat < STALE_AFTER:
        return "running"
    try:
        st = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))["status"]
    except (OSError, json.JSONDecodeError, KeyError):   # absent, mid-replace (Windows)
        st = None
    if st in (None, "running"):
        if beat is None:                          # never beat: still starting?
            young = min(a for a in (_age(run_dir / "checkpoint.json"), _age(run_dir), 1e9)
                        if a is not None)
            if young < STARTUP_GRACE:
                return "running"
        return "crashed" if st == "running" else "unknown"
    return st


def hosted_runs(runs: "str | Path") -> list[Path]:
    """Run dirs under `runs` that are (or were) host-mode runs, most recently
    active first."""
    found = [d for d in Path(runs).iterdir() if mailbox_dir(d).is_dir()] \
        if Path(runs).is_dir() else []
    return sorted(found, key=lambda d: mailbox_dir(d).stat().st_mtime, reverse=True)


def resolve_run(runs: "str | Path", ref: str | None) -> Path:
    """A hosted run by exact id or unique prefix; with no ref, the one live
    hosted run (or, if none is live, the latest — so `next` reports it done).
    Raises ValueError when that's ambiguous: answering the wrong one of two
    live runs must not be possible by omission."""
    runs_ = hosted_runs(runs)
    if ref is not None:
        exact = [d for d in runs_ if d.name == ref]
        hits = exact or [d for d in runs_ if d.name.startswith(ref)]
        if len(hits) != 1:
            raise ValueError(f"no hosted run matches {ref!r}" if not hits else
                             f"{ref!r} matches several runs: "
                             + ", ".join(d.name for d in hits))
        return hits[0]
    if not runs_:
        raise ValueError("no hosted run found — start one with `saage host start`")
    live = [d for d in runs_ if run_status(d) == "running"]
    if len(live) > 1:
        raise ValueError("several hosted runs are live — name one: "
                         + ", ".join(d.name for d in live))
    return live[0] if live else runs_[0]


# --------------------------------------------------------------------------- #
# Host side
# --------------------------------------------------------------------------- #
def read_pending(mailbox: "str | Path") -> dict | None:
    """The outstanding step, or None if the engine isn't waiting on one.
    A step already answered (its result written, the engine not yet woken to
    clear pending.json) is NOT outstanding — so it is never handed out twice."""
    p = Path(mailbox) / "pending.json"
    try:
        pending = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):    # absent, or mid-replace on Windows
        return None
    if (Path(mailbox) / f"result-{pending['seq']}.json").exists():
        return None
    return pending


def submit(run_dir: "str | Path", seq: int, output: str) -> None:
    """Answer step `seq`. Raises ValueError if the run isn't live or `seq`
    isn't the outstanding step — a stale, duplicate, or dead-run submit must
    never be applied (or silently accepted and then ignored)."""
    mailbox = mailbox_dir(run_dir)
    st = run_status(run_dir)
    if st != "running":
        raise ValueError(f"run is not running (status: {st})")
    pending = read_pending(mailbox)
    if pending is None or pending["seq"] != seq:
        have = "none" if pending is None else pending["seq"]
        raise ValueError(f"step {seq} is not pending (outstanding: {have})")
    tmp = _write_tmp(mailbox, {"seq": seq, "output": output})
    try:
        os.link(tmp, mailbox / f"result-{seq}.json")   # exclusive + atomic
    except FileExistsError:
        raise ValueError(f"step {seq} was already answered") from None
    finally:
        os.unlink(tmp)


def wait_next(run_dir: "str | Path", timeout: float) -> tuple[str, object]:
    """Block until the run needs its next agent step — the one wait loop both
    hosts (MCP next_task, `saage host next`) share. Returns ("task", pending)
    | ("done", status) | ("waiting", status) after `timeout` while the engine
    is busy (e.g. a long command step). A dead run's leftover pending.json is
    never handed out: liveness is checked first. completed/failed are final
    at once (the engine wrote them); crashed/unknown must hold for
    STALE_AFTER before they are — a suspended laptop or a stalled disk must
    not end a host's loop while the engine is about to beat again."""
    deadline = time.monotonic() + timeout
    dead_since = None
    while True:
        st = run_status(run_dir)
        if st in ("crashed", "unknown"):
            dead_since = dead_since or time.monotonic()
            if time.monotonic() - dead_since >= STALE_AFTER:
                return "done", st
        elif st != "running":
            return "done", st
        else:
            dead_since = None
        pending = read_pending(mailbox_dir(run_dir)) if st == "running" else None
        if pending is not None:
            return "task", pending
        if time.monotonic() >= deadline:
            return "waiting", st
        time.sleep(_POLL)


# --------------------------------------------------------------------------- #
# Engine side
# --------------------------------------------------------------------------- #
def _beat(path: Path, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            path.touch()
        except OSError:                           # mailbox gone (e.g. test tmp)
            return
        stop.wait(HEARTBEAT_EVERY)


class HostProvider:
    """Delegates each agent step to the host agent via the mailbox.

    `delegates_steps` tells AgentNode to call run_step() with the whole step
    instead of driving the per-turn tool loop: the host runs its own loop with
    its own tools, so saage only ever sees the step's final answer."""

    delegates_steps = True

    def __init__(self, mailbox: "str | Path", timeout: float = DEFAULT_TIMEOUT):
        self.mailbox = Path(mailbox)
        self.timeout = validated_timeout(timeout)
        self.mailbox.mkdir(parents=True, exist_ok=True)
        # one engine per mailbox: a second one (e.g. a bare `saage resume` in
        # another terminal, which picks the newest unfinished run — the live
        # one) would withdraw the first's step and the two would share seqs
        beat_age = _age(self.mailbox / "heartbeat")
        if beat_age is not None and beat_age < STALE_AFTER:
            raise ValueError(
                f"another engine is live on this run's mailbox ({self.mailbox}) "
                f"— wait for it to finish, or stop it first")
        # resume-safe numbering: continue past every seq this mailbox has
        # seen — answered results AND a dead engine's leftover pending step,
        # which is dropped so a late answer to it can never satisfy a new step
        seen = [int(p.stem.split("-", 1)[1]) for p in self.mailbox.glob("result-*.json")]
        stale = read_pending(self.mailbox) or {}
        if stale:
            seen.append(stale["seq"])
        _unlink(self.mailbox / "pending.json")
        self._seq = max(seen, default=0)
        self._stop = threading.Event()
        beat = self.mailbox / "heartbeat"
        beat.touch()                     # live from now: claims the mailbox at once
        self._beater = threading.Thread(target=_beat, args=(beat, self._stop),
                                        daemon=True, name="saage-host-heartbeat")
        self._beater.start()
        atexit.register(self.close)

    def close(self) -> None:
        """Stop beating and say so at once (a clean exit shouldn't make hosts
        wait out STALE_AFTER); a killed engine simply goes stale."""
        self._stop.set()
        self._beater.join(timeout=5)     # else a last touch() can resurrect it
        _unlink(self.mailbox / "heartbeat")

    def run_step(self, step_id: str, skill: str, system: str, task: str,
                 tools: list[str], workspace: str | None = None) -> str:
        self._seq += 1
        seq = self._seq
        result = self.mailbox / f"result-{seq}.json"
        _write_json(self.mailbox / "pending.json", {
            "seq": seq, "step_id": step_id, "skill": skill,
            "system": system, "task": task, "tools": tools,
            # relative paths in the task ("read history.txt") mean files here
            "workspace": workspace})
        log.info("    ⇄ waiting for host agent (step %d)", seq)
        deadline = time.monotonic() + self.timeout
        while not result.exists():
            if time.monotonic() >= deadline:
                _unlink(self.mailbox / "pending.json")
                if result.exists():      # answered just as the deadline hit
                    break
                raise HostTimeout(
                    f"host agent did not answer step {step_id!r} within "
                    f"{self.timeout:.0f}s")
            time.sleep(_POLL)
        out = json.loads(result.read_text(encoding="utf-8"))["output"]
        _unlink(self.mailbox / "pending.json")
        return out

    def complete(self, system, messages, tools):
        # only reached if something drives the per-turn loop directly; treat
        # it as one delegated step whose answer is final (no tool calls)
        return LLMResponse(text=self.run_step(
            "?", "?", system, messages[0]["text"], [t.name for t in tools]))
