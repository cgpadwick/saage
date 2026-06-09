"""Flow B — retry_loop driving a real pytest, with feedback re-injection.

Only the LLM turns are scripted; edit_file and pytest run for real. The first
edit is wrong (a*b), so the real suite fails and the loop retries; the second
edit (a+b) is correct and the real suite passes.
"""
import subprocess
import sys

from saage_testkit import RoutedProvider, call, resp

from saage.hydrate import run_flow


def _scripts():
    return {
        "implement": [
            resp(calls=[call("edit_file", path="calc.py",
                             old="return a - b", new="return a * b")]),  # wrong
            resp("attempted a fix"),
            resp(calls=[call("edit_file", path="calc.py",
                             old="return a * b", new="return a + b")]),  # correct
            resp("fixed it"),
        ],
        "runtests": [
            resp(calls=[call("run_command", command="python -B -m pytest -q")]),
            resp("the test failed. ACTION: fail"),
            resp(calls=[call("run_command", command="python -B -m pytest -q")]),
            resp("all green. ACTION: pass"),
        ],
    }


def test_fix_failing_test(flow_copy):
    flow_yaml = flow_copy("fix_failing_test")
    shared = run_flow(flow_yaml, provider=RoutedProvider(_scripts()))

    # exactly two implement->check attempts (first fails, second passes)
    assert shared["_trace"] == ["implement", "check", "implement", "check"]
    assert shared["_iter"]["fix"] == 1                      # one failed attempt
    assert "return a + b" in (flow_yaml.parent / "calc.py").read_text()

    # the final code really passes the suite (-B avoids stale bytecode)
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q"],
                       cwd=flow_yaml.parent, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
