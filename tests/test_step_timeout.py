"""Command-step timeout: a hung command fails the STEP, never the run.

The dangerous case is the compound command (`python train.py && python
read_score.py`): the direct child is /bin/sh, the real workload a grandchild.
`subprocess.run`'s own timeout kills only the shell and orphans the workload —
it keeps the GPU, keeps writing files under later steps. run_shell's timed path
launches the command as a process-group leader and kills the whole group, and
CommandNode converts expiry into a normal failing step (exit 124) so retry
loops and checks route as usual.
"""
import os
import subprocess
import time

import pytest

from saage.hydrate import build_flow
from saage.nodes import CommandNode
from saage.primitives import retry_loop
from saage.shell import run_shell

posix_only = pytest.mark.skipif(
    os.name == "nt", reason="process-group semantics are POSIX; Windows uses taskkill /T")


# ---------------------------------------------------------------- run_shell --

@posix_only
def test_timeout_kills_the_whole_process_tree(tmp_path):
    # A backgrounded grandchild that would outlive a naive shell-only kill and
    # touch the marker; with a group kill the marker must never appear.
    cmd = "( sleep 1.2; touch orphan_marker ) & sleep 30"
    with pytest.raises(subprocess.TimeoutExpired):
        run_shell(cmd, cwd=tmp_path, timeout=0.4)
    time.sleep(1.6)                       # past the grandchild's write time
    assert not (tmp_path / "orphan_marker").exists()


@posix_only
def test_timeout_attaches_partial_output(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired) as e:
        run_shell("echo early-output; sleep 30", cwd=tmp_path, timeout=0.4)
    assert "early-output" in (e.value.output or "")


def test_timed_command_that_finishes_is_unaffected(tmp_path):
    r = run_shell("echo done", cwd=tmp_path, timeout=30)
    assert r.returncode == 0
    assert r.stdout.strip() == "done"


def test_timed_command_failure_exit_is_preserved(tmp_path):
    assert run_shell("exit 3", cwd=tmp_path, timeout=30).returncode == 3


def test_untimed_path_unchanged(tmp_path):
    r = run_shell("echo plain", cwd=tmp_path)
    assert (r.returncode, r.stdout.strip()) == (0, "plain")


# -------------------------------------------------------------- CommandNode --

@posix_only
def test_command_node_reports_exit_124_and_routes(tmp_path):
    node = CommandNode("slow", "sleep 30", tmp_path, timeout=0.3)
    shared: dict = {}
    action = node.run(shared)
    assert action == "default"            # normal routing, run continues
    out = shared["results"]["slow"]
    assert out["exit"] == 124
    assert "timed out after 0.3s" in out["stderr"]


@posix_only
def test_command_node_without_timeout_never_expires(tmp_path):
    node = CommandNode("quick", "echo hi", tmp_path)   # no timeout arg
    shared: dict = {}
    node.run(shared)
    assert shared["results"]["quick"]["exit"] == 0


@posix_only
def test_timed_out_action_fails_step_not_run(tmp_path):
    """The end-to-end contract: a hung action inside a retry loop times out,
    the deterministic check routes fail, the loop retries and gives up — the
    RUN reaches its normal give-up path instead of hanging forever."""
    action = CommandNode("act", "sleep 30", tmp_path, timeout=0.3)
    check = CommandNode(
        "chk", "test -f done && echo 'ACTION: pass' || echo 'ACTION: fail'",
        tmp_path)
    shared: dict = {}
    retry_loop("loop", action, check, max_iterations=2).run(shared)
    assert shared["results"]["act"]["exit"] == 124
    assert shared["_trace"].count("act") == 2          # retried, then gave up
    assert shared["_iter"]["loop"] == 2


# ------------------------------------------------------------------ hydrate --

def _flow(tmp_path, timeout_yaml: str) -> str:
    f = tmp_path / "flow.yaml"
    f.write_text(
        "workflow:\n"
        f"  - {{ id: t, type: command, run: 'echo ok'{timeout_yaml} }}\n",
        encoding="utf-8")
    return str(f)


def test_hydrate_accepts_numeric_timeout(tmp_path):
    build_flow(_flow(tmp_path, ", timeout: 5"), provider=object(),
               workspace=str(tmp_path))
    build_flow(_flow(tmp_path, ", timeout: 0.5"), provider=object(),
               workspace=str(tmp_path))
    build_flow(_flow(tmp_path, ""), provider=object(),      # absent stays legal
               workspace=str(tmp_path))


@pytest.mark.parametrize("bad", ["'2h'", "true", "0", "-5"])
def test_hydrate_rejects_bad_timeout(tmp_path, bad):
    with pytest.raises(ValueError, match="timeout must be a positive number"):
        build_flow(_flow(tmp_path, f", timeout: {bad}"), provider=object(),
                   workspace=str(tmp_path))
