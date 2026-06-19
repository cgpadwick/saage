"""The three loop primitives, each a PocketFlow Flow factory.

Each returns a `Subflow` — a Flow whose terminal action is normalized so the
whole loop composes as a single node inside a larger flow. Success normalizes to
"default"; "failed" propagates so an outer flow can branch on it.

Every terminal action is wired to an explicit `_End` sink so PocketFlow never
emits a "Flow ends" warning — termination is part of the graph, by design.
"""
from __future__ import annotations

import copy

from pocketflow import Flow, Node

from .nodes import GateNode, LoopGuard, TimeoutGuard, WaitNode

_SUCCESS = {None, "default", "pass", "complete", "exit", "stop"}


class _End(Node):
    """A no-op sink that ends a subflow, yielding a normalized action."""

    def __init__(self, action: str = "default"):
        super().__init__()
        self._action = action

    def post(self, shared, prep_res, exec_res):
        return self._action


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
        # _skip_reset_once is set by build_flow(resume_step=...) on the resumed loop.
        if getattr(self, "_skip_reset_once", False):
            self._skip_reset_once = False
            return None
        for ns, key in self._reset:
            d = shared.get(ns)
            if key is not None and isinstance(d, dict):
                d.pop(key, None)
        return None

    def _orch(self, shared, params=None):
        # A faithful copy of pocketflow.Flow._orch (pinned to <1.0 in pyproject)
        # plus a checkpoint write after each node. If pocketflow's _orch changes,
        # this override must be re-synced or it will silently drop new behaviour.
        # Loop bodies run in their own subflow's _orch, so this yields per-iteration
        # writes inside loops and per-step writes at the top level.
        curr = copy.copy(self.start_node)
        p = params or {**self.params}
        last_action = None
        is_root = getattr(self, "_step_index", None) is None   # the top-level flow
        while curr:
            curr.set_params(p)
            last_action = curr._run(shared)
            nxt_raw = self.get_next_node(curr, last_action)
            if self.sink is not None:
                curr_idx = getattr(curr, "_step_index", None)
                nxt_idx = getattr(nxt_raw, "_step_index", None) if nxt_raw is not None else None
                # If the next node belongs to a different top-level step, record
                # that next index so a crash there resumes at the right step
                # rather than re-running the one that just completed.
                resume_idx = nxt_idx if (nxt_idx is not None and nxt_idx != curr_idx) else curr_idx
                # When the TOP-LEVEL flow finishes its final node, stamp the
                # terminal status in this same atomic write — so a kill between
                # "last node done" and an external status update can't leave a
                # 'running' checkpoint that would redo the final step on resume,
                # and a propagated 'failed' terminal action is recorded as failed.
                if is_root and nxt_raw is None:
                    status = "completed" if last_action in _SUCCESS else "failed"
                else:
                    status = "running"
                self.sink.write(shared, resume_idx, status)
            curr = copy.copy(nxt_raw)
        return last_action

    def post(self, shared, prep_res, last_action):
        return "default" if last_action in _SUCCESS else last_action


def retry_loop(name: str, action, check, max_iterations: int = 3) -> Subflow:
    """action -> check; on 'fail' loop back to action (with feedback) until the
    checker returns 'pass' or max_iterations attempts are exhausted."""
    guard = LoopGuard(name, max_iterations,
                      getattr(action, "id", None), getattr(check, "id", None))
    done = _End()
    action >> check
    check - "pass" >> done
    check - "fail" >> guard
    guard - "again" >> action
    guard - "stop" >> done           # exhausted attempts: give up, continue outer flow
    return Subflow(start=action,
                   reset=[("_iter", name), ("_feedback", getattr(action, "id", None))])


def polling_loop(name: str, poll, classify, interval_seconds: float,
                 max_wait_seconds: float) -> Subflow:
    """poll -> classify; on 'running' wait and poll again, until 'complete' /
    'failed', or the wall-clock cap (max_wait_seconds) trips the guard."""
    wait = WaitNode(interval_seconds)
    guard = TimeoutGuard(name, max_wait_seconds)
    done, failed = _End(), _End("failed")
    poll >> classify
    classify - "complete" >> done
    classify - "failed" >> failed
    classify - "running" >> guard
    guard - "again" >> wait
    guard - "stop" >> failed         # timed out: never completed -> propagate failure
    wait >> poll
    return Subflow(start=poll,
                   reset=[("_iter", name), ("_poll_start", name), ("_poll_count", name)])


def counting_loop(name: str, body: list, max_iterations: int = 10,
                  exit_when: str | None = None) -> Subflow:
    """Run body[0] -> ... -> body[-1] -> gate; loop while under max_iterations
    and exit_when is false."""
    if not body:
        raise ValueError("counting_loop body must have at least one node")
    gate = GateNode(name, max_iterations, exit_when)
    done = _End()
    for a, b in zip(body, body[1:]):
        a >> b
    body[-1] >> gate
    gate - "continue" >> body[0]
    gate - "exit" >> done
    return Subflow(start=body[0], reset=[("_iter", name), ("_exit_reason", name)])
