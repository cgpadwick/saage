"""The kaggle_solver implement_smoke gate: budget enforcement + feedback.

Offline and fast: the timeout path exercises the REAL run_with_timeout.py
helper with a 1s budget (no 120s waits); routing paths substitute the pytest
invocation with cheap stand-ins, keeping the gate's own sh logic intact.
"""
import subprocess
from pathlib import Path

import yaml
from pocketflow import Node

from saage.nodes import CommandNode, render
from saage.primitives import retry_loop

FLOW_DIR = Path(__file__).parent.parent / "flows" / "kaggle_solver"
PYTEST_INVOCATION = 'python3 "{{ flow_dir }}/run_with_timeout.py" 120 python3 -B -m pytest -q tests/'


def _gate_run() -> str:
    """The implement_smoke check's run string, straight from flow.yaml."""
    spec = yaml.safe_load((FLOW_DIR / "flow.yaml").read_text())

    def find(steps, sid):
        for s in steps:
            if s.get("id") == sid:
                return s
            for k in ("action", "check", "poll", "status"):
                if k in s and (r := find([s[k]], sid)):
                    return r
            if "body" in s and (r := find(s["body"], sid)):
                return r

    step = find(spec["workflow"], "implement_smoke")
    assert step and step["timeout"] == 900          # outer hang backstop kept
    return step["run"]


def _run(cmd: str) -> str:
    rendered = render(cmd, {"flow_dir": str(FLOW_DIR)})
    return subprocess.run(["sh", "-c", rendered], capture_output=True,
                          text=True, timeout=60).stdout


def test_gate_times_out_slow_suite_with_actionable_feedback():
    # REAL helper, 1s budget, command that would run 5s: killed -> 124 path
    cmd = _gate_run().replace(PYTEST_INVOCATION,
        'python3 "{{ flow_dir }}/run_with_timeout.py" 1 sleep 5')
    out = _run(cmd)
    assert "ACTION: fail" in out and "ACTION: pass" not in out
    assert "SMOKE SUITE TOO SLOW" in out           # the actionable message...
    assert "slim tests/test_smoke.py" in out       # ...says fix tests,
    assert "Do NOT reimplement" in out             # ...not the experiment


def test_gate_routes_green_red_and_noop_paths():
    for pytest_rc, probe, want in [(0, "true", "ACTION: pass"),
                                   (1, "true", "ACTION: fail"),
                                   (0, "false", "ACTION: fail")]:
        cmd = (_gate_run()
               .replace(PYTEST_INVOCATION, f"(exit {pytest_rc})")
               .replace('python3 "{{ flow_dir }}/no_op_probe.py"', probe))
        out = _run(cmd)
        assert want in out
        assert "TOO SLOW" not in out               # message is timeout-only


class _NoopAction(Node):
    """Stands in for the implement agent; the loop only needs its id."""
    def __init__(self):
        super().__init__()
        self.id = "implement"


def test_slow_suite_message_reaches_retry_feedback(tmp_path):
    # end-to-end through the engine: a timed-out suite must land the
    # actionable message in _feedback["implement"], which AgentNode.prep
    # injects into the next implement attempt (covered by the engine tests)
    cmd = _gate_run().replace(PYTEST_INVOCATION,
        'python3 "{{ flow_dir }}/run_with_timeout.py" 1 sleep 5')
    check = CommandNode("implement_smoke", cmd, tmp_path)
    flow = retry_loop("implement_loop", _NoopAction(), check, max_iterations=2)
    shared = {"flow_dir": str(FLOW_DIR)}
    flow.run(shared)
    fb = shared["_feedback"]["implement"]
    assert "SMOKE SUITE TOO SLOW" in fb["stdout"]
    assert shared["_iter"]["implement_loop"] == 2   # capped, never passed
