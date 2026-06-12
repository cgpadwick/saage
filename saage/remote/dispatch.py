"""dispatch_many — fan N jobs out over K boxes, supervise, survive — H2.

One *job* = one run of a flow, distinguished by its ``--set`` overrides.
The dispatcher owns what a human owned before: slot accounting against each
target's ``max_runs``, provisioning each node once (H6), concurrent
handoffs, polling (mirror-first, H3), per-job deadlines, dead-box
detection, lost-job requeue, and target quarantine.

Failure semantics (docs/batched_hillclimb_plan.md §4):

- job crashes fast        -> node says "failed"      -> resolved, no retry
- job hangs               -> deadline kill           -> "timeout", requeued
                                                        while attempts remain
- box dies under us       -> mirror heartbeat stale
                             AND ssh unreachable     -> jobs "lost", requeued
                                                        elsewhere; node dead
- handoff/bootstrap fails -> attempt returned to the queue; two consecutive
                             handoff failures quarantine the target
- provision fails         -> target quarantined before any job lands on it
- nowhere left to run     -> remaining queued jobs end "stranded"

Jobs must be replayable (pure functions of their --set inputs): every
attempt gets a fresh run_id, and nothing here writes shared state on the
job's behalf — collection is the caller's move, after resolution.
"""
from __future__ import annotations

import logging
import time as _time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .creds import Target
from .handoff import handoff
from .observe import fetch_run, kill_run, poll_run
from .provision import provision_node
from .state import RunState
from .target import SshTarget

log = logging.getLogger("saage.remote")

FINAL = {"done", "failed", "timeout", "killed", "lost", "stranded", "error"}
_RETRYABLE = {"timeout", "lost"}        # node said nothing conclusive about the job


class DispatchError(RuntimeError):
    pass


@dataclass
class Job:
    """One unit of work: a flow run distinguished by its --set overrides."""
    name: str
    set_args: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    max_attempts: int = 2
    # supervised state
    status: str = "queued"      # queued|dispatching|running|<FINAL>
    target: str | None = None
    run_id: str | None = None
    run_ids: list = field(default_factory=list)   # every attempt, in order
    attempts: int = 0
    started: float | None = None                  # clock seconds, current attempt
    error: str = ""

    @property
    def resolved(self) -> bool:
        return self.status in FINAL


class Ops:
    """Side-effect seam: everything the dispatcher does to the world.
    Tests substitute this whole object; production uses the real layers."""

    def handoff(self, flow: str, target: Target, set_args: dict,
                env: dict, **kw) -> str:
        rs = handoff(flow=flow, target=target, set_args=set_args,
                     extra_env=env, **kw)
        return rs.run_id

    def poll(self, run_id: str) -> dict:
        return poll_run(RunState(run_id))

    def kill(self, run_id: str) -> None:
        kill_run(RunState(run_id))

    def fetch(self, run_id: str, dest: Path) -> None:
        fetch_run(RunState(run_id), dest)

    def provision(self, target: Target, command: str, *,
                  files: Path | None) -> None:
        provision_node(target, command, files=files)

    def node_reachable(self, target: Target) -> bool:
        try:
            SshTarget(target).conn.run("true", timeout=30)
            return True
        except Exception:
            return False


@dataclass
class _Node:
    target: Target
    in_flight: int = 0              # jobs we put there, dispatching+running
    consec_handoff_failures: int = 0
    quarantined: str = ""           # truthy = reason

    @property
    def usable(self) -> bool:
        return not self.quarantined

    @property
    def free(self) -> int:
        return max(0, self.target.max_runs - self.in_flight)


class Dispatcher:
    """The loop. Synchronous and single-threaded except handoffs, which run
    in a small thread pool (they block for minutes of bootstrap each)."""

    def __init__(self, flow: str, jobs: list[Job], targets: list[Target], *,
                 ops: Ops | None = None,
                 provision_cmd: str | None = None,
                 provision_files: Path | None = None,
                 max_hours: float | None = None,
                 poll_interval: float = 60.0,
                 stale_after: float = 300.0,
                 fetch_dest: Path | None = None,
                 dispatch_workers: int = 4,
                 quarantine_after: int = 2,
                 handoff_opts: dict | None = None,
                 clock=_time):
        if not targets:
            raise DispatchError("no targets to dispatch to")
        self.flow = flow
        self.jobs = jobs
        self.nodes = [_Node(t) for t in targets]
        self.ops = ops or Ops()
        self.provision_cmd = provision_cmd
        self.provision_files = provision_files
        self.max_seconds = max_hours * 3600 if max_hours else None
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.fetch_dest = fetch_dest
        self.quarantine_after = quarantine_after
        self.handoff_opts = handoff_opts or {}
        self.clock = clock
        self._pool = ThreadPoolExecutor(max_workers=dispatch_workers)
        self._futures: dict[Future, tuple[Job, _Node]] = {}

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> list[Job]:
        try:
            self._provision_all()
            while not all(j.resolved for j in self.jobs):
                self._assign()
                self._reap_handoffs()
                if all(j.resolved for j in self.jobs):
                    break
                self._strand_if_nowhere_to_run()
                if all(j.resolved for j in self.jobs):
                    break
                self.clock.sleep(self.poll_interval)
                self._poll_running()
                self._check_node_liveness()
            return self.jobs
        finally:
            self._pool.shutdown(wait=False)

    # -- phases of one tick ----------------------------------------------------

    def _provision_all(self) -> None:
        if not self.provision_cmd:
            return
        for node in self.nodes:
            try:
                self.ops.provision(node.target, self.provision_cmd,
                                   files=self.provision_files)
            except Exception as exc:    # any failure = no jobs land there
                node.quarantined = f"provision failed: {exc}"
                log.warning("target %s quarantined: %s",
                            node.target.name, node.quarantined)

    def _assign(self) -> None:
        for job in self.jobs:
            if job.status != "queued":
                continue
            node = self._pick_node()
            if node is None:
                return                       # no free slot this tick
            job.status, job.target = "dispatching", node.target.name
            job.attempts += 1
            node.in_flight += 1
            fut = self._pool.submit(
                self.ops.handoff, self.flow, node.target,
                dict(job.set_args), dict(job.env), **self.handoff_opts)
            self._futures[fut] = (job, node)
            log.info("job %s -> %s (attempt %d)", job.name,
                     node.target.name, job.attempts)

    def _pick_node(self) -> _Node | None:
        usable = [n for n in self.nodes if n.usable and n.free > 0]
        if not usable:
            return None
        # most free slots first: spreads load, keeps a box free for requeues
        return max(usable, key=lambda n: n.free)

    def _reap_handoffs(self) -> None:
        for fut in [f for f in self._futures if f.done()]:
            job, node = self._futures.pop(fut)
            try:
                job.run_id = fut.result()
                job.run_ids.append(job.run_id)
                job.status, job.started = "running", self.clock.time()
                node.consec_handoff_failures = 0
            except Exception as exc:    # HandoffError, PreflightError, ssh…
                node.in_flight -= 1
                node.consec_handoff_failures += 1
                if node.consec_handoff_failures >= self.quarantine_after:
                    node.quarantined = f"{node.consec_handoff_failures} consecutive handoff failures"
                    log.warning("target %s quarantined: %s",
                                node.target.name, node.quarantined)
                self._retry_or_finalize(job, "error", f"handoff failed: {exc}")

    def _poll_running(self) -> None:
        for job in self.jobs:
            if job.status != "running":
                continue
            node = self._node_of(job)
            got = self.ops.poll(job.run_id)
            phase = got.get("phase")
            if phase in ("done", "failed"):
                self._resolve(job, node, phase)
            elif phase in ("timeout", "killed"):
                # node watchdog or an outside hand — same retry semantics
                self._retry_or_finalize(job, "timeout", f"node phase {phase}",
                                        node=node)
            elif self._over_deadline(job):
                try:
                    self.ops.kill(job.run_id)
                except Exception as exc:                  # box may be gone
                    log.warning("deadline kill of %s failed: %s", job.run_id, exc)
                self._retry_or_finalize(job, "timeout", "deadline exceeded",
                                        node=node)

    def _check_node_liveness(self) -> None:
        """Dead box = every heartbeat we can see is stale AND ssh says no.
        Two signals on purpose: a flaky ssh path with a fresh mirror
        heartbeat is NOT a dead box, and vice versa."""
        for node in self.nodes:
            running = [j for j in self.jobs
                       if j.status == "running" and j.target == node.target.name]
            if not running:
                continue
            heartbeats = [self.ops.poll(j.run_id) for j in running]
            fresh = any(self._is_fresh(h) for h in heartbeats)
            if fresh or self.ops.node_reachable(node.target):
                continue
            node.quarantined = "box unreachable and heartbeats stale"
            log.warning("target %s declared dead: %s",
                        node.target.name, node.quarantined)
            for job in running:
                self._retry_or_finalize(job, "lost", "box died under the run",
                                        node=node)

    def _strand_if_nowhere_to_run(self) -> None:
        if any(n.usable for n in self.nodes):
            return
        for job in self.jobs:
            if job.status == "queued":
                job.status = "stranded"
                job.error = "no usable targets left"
                log.error("job %s stranded: %s", job.name, job.error)

    # -- helpers ---------------------------------------------------------------

    def _node_of(self, job: Job) -> _Node | None:
        return next((n for n in self.nodes
                     if n.target.name == job.target), None)

    def _over_deadline(self, job: Job) -> bool:
        return (self.max_seconds is not None and job.started is not None
                and self.clock.time() - job.started > self.max_seconds)

    def _is_fresh(self, heartbeat: dict) -> bool:
        updated = heartbeat.get("updated")
        if not updated:
            return False
        try:
            from datetime import datetime, timezone
            then = datetime.strptime(updated, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
        except (ValueError, TypeError):
            return False
        return self.clock.time() - then < self.stale_after

    def _retry_or_finalize(self, job: Job, status: str, why: str,
                           node: _Node | None = None) -> None:
        if node is not None:
            node.in_flight -= 1
        job.error = why
        retryable = status in _RETRYABLE or status == "error"
        if retryable and job.attempts < job.max_attempts:
            job.status, job.run_id, job.started = "queued", None, None
            log.info("job %s requeued after %s (%s)", job.name, status, why)
        else:
            job.status = status
            log.info("job %s final: %s (%s)", job.name, status, why)
            self._fetch_quietly(job)

    def _resolve(self, job: Job, node: _Node | None, phase: str) -> None:
        if node is not None:
            node.in_flight -= 1
        job.status = phase
        log.info("job %s final: %s", job.name, phase)
        self._fetch_quietly(job)

    def _fetch_quietly(self, job: Job) -> None:
        if self.fetch_dest is None or not job.run_id:
            return
        try:
            self.ops.fetch(job.run_id, Path(self.fetch_dest) / job.name)
        except Exception as exc:                          # box AND mirror gone
            log.warning("fetch for %s failed: %s", job.name, exc)


def dispatch_many(flow: str, jobs: list[Job], targets: list[Target],
                  **kwargs) -> list[Job]:
    """Run every job to a final state across the targets. See Dispatcher."""
    return Dispatcher(flow, jobs, targets, **kwargs).run()
