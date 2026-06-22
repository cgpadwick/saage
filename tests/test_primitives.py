"""Unit tests for the three loop primitives, using tiny fake nodes (no LLM)."""
from pocketflow import Node

from saage.primitives import Subflow, counting_loop, polling_loop, retry_loop


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


def test_counting_loop_max_iterations_quoted_string():
    # a YAML-quoted "3" must coerce to int, not crash GateNode with int >= str
    flow = counting_loop("c", [Tracer("a")], max_iterations="3")
    shared = {}
    flow.run(shared)
    assert shared["_iter"]["c"] == 3
    assert shared["_exit_reason"]["c"] == "max_iterations"


def test_counting_loop_max_iterations_template_resolved_from_shared():
    # a templated bound is resolved at run time against the live shared store
    flow = counting_loop("c", [Tracer("a")], max_iterations="{{ num_runs }}")
    shared = {"num_runs": 2}
    flow.run(shared)
    assert shared["_iter"]["c"] == 2
    assert shared["_exit_reason"]["c"] == "max_iterations"


def test_counting_loop_max_iterations_unresolvable_raises_clear_error():
    import pytest
    flow = counting_loop("c", [Tracer("a")], max_iterations="not-a-number")
    with pytest.raises(ValueError, match="max_iterations must resolve to an integer"):
        flow.run({})


def test_counting_loop_max_iterations_invalid_template_raises_clear_error():
    # a malformed Jinja template must surface the clear config error, not a raw
    # TemplateSyntaxError
    import pytest
    flow = counting_loop("c", [Tracer("a")], max_iterations="{{ bad")
    with pytest.raises(ValueError, match="not a valid number or template"):
        flow.run({})


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


class AlwaysFail(Node):
    def __init__(self, id):
        super().__init__()
        self.id = id

    def post(self, shared, prep_res, exec_res):
        shared.setdefault("_trace", []).append(self.id)
        return "fail"


def test_nested_retry_loop_resets_each_outer_iteration():
    # a retry_loop nested inside a counting_loop must get a FRESH attempt budget
    # every outer iteration (its counter is reset on subflow entry).
    inner = retry_loop("inner", Tracer("act"), AlwaysFail("chk"), max_iterations=2)
    outer = counting_loop("outer", [inner], max_iterations=3)
    shared = {}
    outer.run(shared)
    # 2 attempts per inner run × 3 outer iterations = 6. Without the per-entry
    # reset the stale counter would yield only 4 (2 + 1 + 1).
    assert shared["_trace"].count("act") == 6
    assert shared["_iter"]["outer"] == 3


def test_subflow_normalizes_success_action():
    # a primitive composes as a single node returning "default" on success
    flow = counting_loop("c", [Tracer("a")], max_iterations=1)
    assert isinstance(flow, Subflow)
    shared = {}
    assert flow.post(shared, None, "exit") == "default"
    assert flow.post(shared, None, "failed") == "failed"


# --------------------------------------------------------------------------- #
# action-role nodes must not let stray ACTION prose end the flow
# --------------------------------------------------------------------------- #
def test_action_node_stray_action_falls_through_to_default():
    """An action agent narrating "...run ACTION: python predict.py..." parses
    to action 'python'. As a retry_loop action it has only a 'default' edge to
    the check, so it must route there — not silently END the flow (the live
    kaggle_solver bug: the run died one step before submission)."""
    from saage.nodes import _route, _parse_action
    from pocketflow import Node

    class Emitter(Node):
        """An action node whose post returns a routed, parsed action."""
        def __init__(self, id, text):
            super().__init__(); self.id = id; self._text = text
        def post(self, shared, prep_res, exec_res):
            shared.setdefault("_trace", []).append(self.id)
            return _route(self, _parse_action(self._text))

    # the agent's final message contains stray ACTION prose
    action = Emitter("implement", "Done. Next, run `ACTION: python` predict.py.")
    check = FailUntil("smoke", pass_on=1)        # passes immediately
    flow = retry_loop("impl", action, check, max_iterations=3)
    shared = {}
    flow.run(shared)

    # the loop completed: action -> check -> done, no silent flow-death
    # (pre-fix, the flow ended at 'implement' and 'smoke' never ran)
    assert shared["_trace"] == ["implement", "smoke"]


def test_route_leaves_decider_actions_untouched():
    """A check node with named pass/fail edges and no 'default' keeps its
    routing — the fallback only applies when a 'default' edge exists."""
    from saage.nodes import _route
    from pocketflow import Node

    check = Node()
    other = Node()
    check - "pass" >> other
    check - "fail" >> other
    assert _route(check, "pass") == "pass"
    assert _route(check, "fail") == "fail"
    # no 'default' edge -> unknown action passes through unchanged (not masked)
    assert _route(check, "weird") == "weird"
