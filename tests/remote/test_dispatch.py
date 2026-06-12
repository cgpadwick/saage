"""H2: the dispatcher + reaper. Offline: Ops is replaced wholesale, the
clock is simulated, handoffs are instant. Each test is one row of the
failure taxonomy in docs/batched_hillclimb_plan.md §4."""
from __future__ import annotations

import threading
import time as _time
from datetime import datetime, timezone

import pytest

from saage.remote.creds import Target
from saage.remote.dispatch import Dispatcher, Job

T0 = 1_750_000_000.0


class FakeClock:
    def __init__(self):
        self.t = T0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt
        _time.sleep(0.002)          # let handoff threads get scheduled


class FakeOps:
    """Scripted world. Default: every handoff works, every run is 'running'
    once then 'done', heartbeats always fresh, every box reachable."""

    def __init__(self, clock, *, fail_handoff=(), provision_fail=(),
                 unreachable=(), phase_for=None, stale=()):
        self.clock = clock
        self.fail_handoff = set(fail_handoff)
        self.provision_fail = set(provision_fail)
        self.unreachable = set(unreachable)
        self.stale = set(stale)             # target names with stale heartbeats
        self.phase_for = phase_for          # (target, nth_poll) -> phase | None
        self.lock = threading.Lock()
        self.handoffs, self.kills, self.fetches, self.provisions = [], [], [], []
        self.active, self.max_active = {}, {}
        self._n = 0
        self._polls = {}
        self._resolved = set()

    # -- Ops surface -----------------------------------------------------------

    def handoff(self, flow, target, set_args, env, **kw):
        with self.lock:
            if target.name in self.fail_handoff:
                raise RuntimeError(f"bootstrap failed on {target.name}")
            self._n += 1
            rid = f"r{self._n}@{target.name}"
            self.handoffs.append((target.name, dict(set_args)))
            self.active[target.name] = self.active.get(target.name, 0) + 1
            self.max_active[target.name] = max(
                self.max_active.get(target.name, 0), self.active[target.name])
            return rid

    def poll(self, rid):
        target = rid.split("@")[1]
        with self.lock:
            n = self._polls[rid] = self._polls.get(rid, 0) + 1
        phase = (self.phase_for(target, n) if self.phase_for
                 else ("running" if n == 1 else "done"))
        if phase in ("done", "failed", "timeout", "killed"):
            self._mark_resolved(rid, target)
        updated = "2020-01-01T00:00:00Z" if target in self.stale else \
            datetime.fromtimestamp(self.clock.time(), tz=timezone.utc
                                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"phase": phase, "updated": updated, "source": "bucket"}

    def kill(self, rid):
        self.kills.append(rid)
        self._mark_resolved(rid, rid.split("@")[1])

    def fetch(self, rid, dest):
        self.fetches.append((rid, str(dest)))

    def provision(self, target, command, *, files):
        self.provisions.append(target.name)
        if target.name in self.provision_fail:
            raise RuntimeError(f"dataset pull failed on {target.name}")

    def node_reachable(self, target):
        return target.name not in self.unreachable

    def _mark_resolved(self, rid, target):
        with self.lock:
            if rid not in self._resolved:
                self._resolved.add(rid)
                self.active[target] -= 1


def _targets(**caps) -> list[Target]:
    return [Target(name=n, host=f"{n}.host", max_runs=c)
            for n, c in caps.items()]


def _jobs(n) -> list[Job]:
    return [Job(name=f"j{i}", set_args={"seed": str(i)}) for i in range(n)]


def _dispatch(jobs, targets, ops, clock, **kw):
    kw.setdefault("poll_interval", 60)
    return Dispatcher("flow.yaml", jobs, targets, ops=ops, clock=clock,
                      **kw).run()


# --------------------------------------------------------------------------- #

def test_five_jobs_two_boxes_within_caps():
    clock = FakeClock()
    ops = FakeOps(clock)
    jobs = _dispatch(_jobs(5), _targets(a=2, b=3), ops, clock)
    assert all(j.status == "done" for j in jobs)
    assert len(ops.handoffs) == 5
    assert ops.max_active.get("a", 0) <= 2          # capacity respected,
    assert ops.max_active.get("b", 0) <= 3          # at every moment
    assert all(j.run_id for j in jobs)


def test_jobs_queue_when_slots_full_then_drain():
    clock = FakeClock()
    ops = FakeOps(clock)
    jobs = _dispatch(_jobs(6), _targets(a=1), ops, clock)
    assert all(j.status == "done" for j in jobs)
    assert ops.max_active["a"] == 1                  # strictly serial box


def test_handoff_failures_quarantine_target_and_jobs_finish_elsewhere():
    clock = FakeClock()
    ops = FakeOps(clock, fail_handoff={"bad"})
    jobs = _dispatch(_jobs(3), _targets(bad=2, ok=2), ops, clock,
                     quarantine_after=2)
    assert all(j.status == "done" for j in jobs)
    assert all(j.target == "ok" for j in jobs)


def test_fast_crash_is_final_no_retry():
    clock = FakeClock()
    ops = FakeOps(clock, phase_for=lambda t, n: "failed")
    jobs = _dispatch(_jobs(1), _targets(a=1), ops, clock)
    assert jobs[0].status == "failed"
    assert jobs[0].attempts == 1                     # the node's word is final


def test_hung_job_deadline_kill_requeue_then_timeout():
    clock = FakeClock()
    ops = FakeOps(clock, phase_for=lambda t, n: "running")   # wedged forever
    jobs = _dispatch(_jobs(1), _targets(a=1), ops, clock,
                     max_hours=0.5, poll_interval=1800)
    assert jobs[0].status == "timeout"
    assert jobs[0].attempts == 2                     # requeued once, then final
    assert len(ops.kills) == 2
    assert len(jobs[0].run_ids) == 2                 # fresh run_id per attempt


def test_dead_box_jobs_lost_then_requeued_on_survivor():
    clock = FakeClock()
    # box 'dying' never finishes anything, goes stale and unreachable;
    # box 'ok' completes normally
    ops = FakeOps(clock, unreachable={"dying"}, stale={"dying"},
                  phase_for=lambda t, n: "running" if t == "dying" else (
                      "running" if n == 1 else "done"))
    jobs = _dispatch(_jobs(2), _targets(dying=2), ops, clock)
    # all on 'dying', which dies — requeued, but no survivor: stranded
    assert all(j.status == "stranded" for j in jobs)

    clock2 = FakeClock()
    ops2 = FakeOps(clock2, unreachable={"dying"}, stale={"dying"},
                   phase_for=lambda t, n: "running" if t == "dying" else (
                       "running" if n == 1 else "done"))
    # bigger slot count on the survivor so requeues land there
    jobs2 = _dispatch(_jobs(2), _targets(dying=2, ok=2), ops2, clock2)
    by_status = {j.status for j in jobs2}
    assert by_status == {"done"}
    assert all(j.target == "ok" for j in jobs2)      # finished on the survivor


def test_fresh_heartbeat_protects_unreachable_box():
    clock = FakeClock()
    # ssh is down but the mirror heartbeat is fresh: NOT dead, runs finish
    ops = FakeOps(clock, unreachable={"a"})
    jobs = _dispatch(_jobs(2), _targets(a=2), ops, clock)
    assert all(j.status == "done" for j in jobs)


def test_provision_failure_quarantines_before_any_dispatch():
    clock = FakeClock()
    ops = FakeOps(clock, provision_fail={"bad"})
    jobs = _dispatch(_jobs(2), _targets(bad=4, ok=2), ops, clock,
                     provision_cmd="pull dataset")
    assert all(j.status == "done" and j.target == "ok" for j in jobs)
    assert ops.handoffs and all(t == "ok" for t, _ in ops.handoffs)
    assert sorted(ops.provisions) == ["bad", "ok"]


def test_everything_dead_strands_the_queue():
    clock = FakeClock()
    ops = FakeOps(clock, provision_fail={"a", "b"})
    jobs = _dispatch(_jobs(3), _targets(a=1, b=1), ops, clock,
                     provision_cmd="pull dataset")
    assert all(j.status == "stranded" for j in jobs)
    assert not ops.handoffs


def test_artifacts_fetched_per_resolved_job(tmp_path):
    clock = FakeClock()
    ops = FakeOps(clock)
    jobs = _dispatch(_jobs(2), _targets(a=2), ops, clock, fetch_dest=tmp_path)
    assert all(j.status == "done" for j in jobs)
    fetched_to = sorted(d for _, d in ops.fetches)
    assert fetched_to == [str(tmp_path / "j0"), str(tmp_path / "j1")]


def test_set_args_pass_through_per_job():
    clock = FakeClock()
    ops = FakeOps(clock)
    _dispatch(_jobs(3), _targets(a=3), ops, clock)
    assert sorted(s["seed"] for _, s in ops.handoffs) == ["0", "1", "2"]


def test_timeout_is_final_when_retries_disabled():
    clock = FakeClock()
    ops = FakeOps(clock, phase_for=lambda t, n: "running")   # wedged forever
    jobs = [Job(name="j0", retry_timeouts=False)]
    out = _dispatch(jobs, _targets(a=1), ops, clock,
                    max_hours=0.5, poll_interval=1800)
    assert out[0].status == "timeout"
    assert out[0].attempts == 1                 # no second stall of the barrier
    assert len(ops.kills) == 1
