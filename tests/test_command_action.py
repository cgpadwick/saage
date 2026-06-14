"""CommandNode ACTION parsing (E2): deterministic loop checks from commands.

A command step that prints `ACTION: pass|fail|...` drives loop routing exactly
like an agent skill ending its reply with one — this is what lets a pytest
smoke run or a submission validator act as a retry_loop check with no LLM.
Commands that print no ACTION keep returning "default" (unchanged behavior).
"""
from saage.nodes import CommandNode
from saage.primitives import retry_loop


def _run(node, shared=None):
    shared = shared if shared is not None else {}
    action = node.run(shared)
    return action, shared


def test_no_action_line_returns_default(tmp_path):
    action, shared = _run(CommandNode("c", "echo hello", tmp_path))
    assert action == "default"
    assert shared["results"]["c"]["exit"] == 0


def test_action_pass_routes_pass(tmp_path):
    action, _ = _run(CommandNode("c", "echo 'ACTION: pass'", tmp_path))
    assert action == "pass"


def test_last_action_wins(tmp_path):
    action, _ = _run(CommandNode(
        "c", "echo 'ACTION: fail'; echo 'more output'; echo 'ACTION: pass'", tmp_path))
    assert action == "pass"


def test_action_tolerates_decoration_and_case(tmp_path):
    action, _ = _run(CommandNode("c", "echo '**action: Fail**'", tmp_path))
    assert action == "Fail"


def test_action_parsed_even_on_nonzero_exit(tmp_path):
    # the shell `|| echo "ACTION: fail"` idiom: the check command failed but
    # the step itself still routes deterministically
    action, shared = _run(CommandNode("c", "false || echo 'ACTION: fail'", tmp_path))
    assert action == "fail"
    assert shared["results"]["c"]["exit"] == 0


def test_captures_still_work_alongside_action(tmp_path):
    action, shared = _run(CommandNode(
        "c", "echo 'SCORE=0.91'; echo 'ACTION: pass'", tmp_path,
        captures={"score": r"SCORE=([0-9.]+)"}))
    assert action == "pass"
    assert shared["score"] == 0.91


def test_command_as_retry_loop_check(tmp_path):
    """The port's core pattern: agent action + deterministic command check."""
    marker = tmp_path / "ok"
    action = CommandNode("act", f"touch {marker}", tmp_path)
    check = CommandNode(
        "chk", f"test -f {marker} && echo 'ACTION: pass' || echo 'ACTION: fail'",
        tmp_path)
    shared: dict = {}
    retry_loop("smoke", action, check, max_iterations=3).run(shared)
    assert shared["_trace"].count("chk") == 1          # passed on first try
    assert shared["_trace"].count("act") == 1          # no retry happened
