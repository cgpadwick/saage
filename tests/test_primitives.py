"""Unit tests for the three loop primitives, using tiny fake nodes (no LLM)."""
from pocketflow import Node

from cwe.primitives import Subflow, counting_loop, polling_loop, retry_loop


class Tracer(Node):
    def __init__(self, id, action="default"):
        super().__init__()
        self.id = id
        self._action = action

    def post(self, shared, prep_res, exec_res):
        shared.setdefault("_trace", []).append(self.id)
        return self._action


class FailUntil(Node):
    """Returns 'fail' until it has been called `pass_on` times, then 'pass'."""
    def __init__(self, id, pass_on):
        super().__init__()
        self.id = id
        self.pass_on = pass_on

    def post(self, shared, prep_res, exec_res):
        shared.setdefault("_trace", []).append(self.id)
        n = shared.get("attempts", 0) + 1
        shared["attempts"] = n
        return "pass" if n >= self.pass_on else "fail"


class Bump(Node):
    def __init__(self, id):
        super().__init__()
        self.id = id

    def post(self, shared, prep_res, exec_res):
        shared.setdefault("_trace", []).append(self.id)
        shared["score"] = shared.get("score", 0) + 1
        return "default"


class PollClassify(Node):
    def __init__(self, id, complete_at):
        super().__init__()
        self.id = id
        self.complete_at = complete_at

    def post(self, shared, prep_res, exec_res):
        shared.setdefault("_trace", []).append(self.id)
        n = shared.get("polls", 0) + 1
        shared["polls"] = n
        return "complete" if n >= self.complete_at else "running"


# --------------------------------------------------------------------------- #
# retry_loop
# --------------------------------------------------------------------------- #
def test_retry_loop_stops_on_pass():
    flow = retry_loop("r", Tracer("act"), FailUntil("chk", pass_on=3), max_iterations=5)
    shared = {}
    flow.run(shared)
    assert shared["_trace"].count("act") == 3      # retried until pass
    assert shared["_iter"]["r"] == 2               # two failures


def test_retry_loop_honors_max_iterations():
    flow = retry_loop("r", Tracer("act"), FailUntil("chk", pass_on=99), max_iterations=2)
    shared = {}
    flow.run(shared)
    assert shared["_trace"].count("act") == 2      # never passes; capped
    assert shared["_iter"]["r"] == 2


# --------------------------------------------------------------------------- #
# counting_loop
# --------------------------------------------------------------------------- #
def test_counting_loop_runs_max_iterations():
    flow = counting_loop("c", [Tracer("a"), Tracer("b")], max_iterations=3)
    shared = {}
    flow.run(shared)
    assert shared["_trace"] == ["a", "b"] * 3
    assert shared["_iter"]["c"] == 3
    assert shared["_exit_reason"]["c"] == "max_iterations"


def test_counting_loop_exits_on_predicate():
    flow = counting_loop("c", [Bump("a")], max_iterations=10, exit_when="score >= 3")
    shared = {}
    flow.run(shared)
    assert shared["score"] == 3
    assert shared["_iter"]["c"] == 3
    assert shared["_exit_reason"]["c"] == "exit_when"


def test_counting_loop_undefined_exit_name_does_not_crash():
    # exit_when references `accuracy`, which no step has populated yet. The loop
    # must treat the predicate as not-yet-satisfied and run to max_iterations,
    # rather than raising NameError on the first gate evaluation.
    flow = counting_loop("c", [Tracer("a")], max_iterations=2,
                         exit_when="accuracy >= target")
    shared = {}
    flow.run(shared)
    assert shared["_iter"]["c"] == 2
    assert shared["_exit_reason"]["c"] == "max_iterations"


# --------------------------------------------------------------------------- #
# polling_loop
# --------------------------------------------------------------------------- #
def test_polling_loop_terminates_on_complete():
    flow = polling_loop("p", Tracer("poll"), PollClassify("cls", complete_at=3),
                        interval_seconds=0, max_wait_seconds=60)
    shared = {}
    flow.run(shared)
    assert shared["_trace"].count("poll") == 3
    assert shared["_trace"][-1] == "cls"


def test_polling_loop_timeout_cap_prevents_hang():
    # classify never completes, but the wall-clock cap stops it
    flow = polling_loop("p", Tracer("poll"), PollClassify("cls", complete_at=999),
                        interval_seconds=0, max_wait_seconds=0)
    shared = {}
    flow.run(shared)                                # must return, not hang
    assert shared["_trace"].count("poll") == 1


def test_subflow_normalizes_success_action():
    # a primitive composes as a single node returning "default" on success
    flow = counting_loop("c", [Tracer("a")], max_iterations=1)
    assert isinstance(flow, Subflow)
    shared = {}
    assert flow.post(shared, None, "exit") == "default"
    assert flow.post(shared, None, "failed") == "failed"
