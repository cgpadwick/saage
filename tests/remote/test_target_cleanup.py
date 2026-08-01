"""`saage remote list` / `cleanup`, ps progress lines, terminate unregisters.

All offline: SshTarget is stubbed where a probe would happen, input() is
scripted, the Lambda API is faked.
"""
from __future__ import annotations

import argparse

import pytest

from saage.remote import observe
from saage.remote.creds import add_target, list_targets
from saage.remote.state import RunState


# -- cleanup -------------------------------------------------------------------

def _answers(*replies):
    it = iter(replies)
    return lambda prompt: next(it)


def test_cleanup_removes_on_yes_keeps_on_no(saage_home, capsys):
    # prompts run in sorted order: a-dead first, then b-alive
    add_target("b-alive", "h2")
    add_target("a-dead", "h1")
    observe.cleanup(ask=_answers("y", "n"))
    assert set(list_targets()) == {"b-alive"}


def test_cleanup_default_answer_keeps(saage_home):
    add_target("a", "h1")
    observe.cleanup(ask=_answers(""))
    assert set(list_targets()) == {"a"}


def test_cleanup_warns_when_active_run_uses_target(saage_home, capsys):
    add_target("busy", "h1")
    RunState.create("run-123").update(target="busy", phase="running")
    observe.cleanup(ask=_answers("n"))
    out = capsys.readouterr().out
    assert "run-123" in out          # the active run is named in the warning


def test_cleanup_no_warning_for_finished_run(saage_home, capsys):
    add_target("idle", "h1")
    RunState.create("run-999").update(target="idle", phase="done")
    observe.cleanup(ask=_answers("n"))
    assert "run-999" not in capsys.readouterr().out


def test_cleanup_reminds_removal_is_not_termination(saage_home, capsys):
    add_target("a", "h1")
    observe.cleanup(ask=_answers("y"))
    out = capsys.readouterr().out
    assert "terminate" in out        # billing reminder after a removal


def test_cleanup_no_targets(saage_home, capsys):
    assert observe.cleanup(ask=_answers()) == 0
    assert "no targets" in capsys.readouterr().out


class _StubNode:
    """Stands in for SshTarget: reachable targets return sessions, dead raise."""
    reachable: dict[str, list[str]] = {}

    def __init__(self, target):
        self.name = target.name

    def sessions(self):
        if self.name in self.reachable:
            return self.reachable[self.name]
        raise OSError("connect timeout")


def test_cleanup_check_shows_reachability(saage_home, capsys, monkeypatch):
    add_target("up", "h1")
    add_target("down", "h2")
    monkeypatch.setattr(observe, "SshTarget", _StubNode)
    _StubNode.reachable = {"up": []}
    observe.cleanup(check=True, ask=_answers("n", "n"))
    out = capsys.readouterr().out
    assert "reachable" in out
    assert "unreachable" in out


# -- ps progress ---------------------------------------------------------------

def test_ps_prints_progress_per_target(saage_home, capsys, monkeypatch):
    add_target("up", "h1", user="u")
    add_target("down", "h2")
    monkeypatch.setattr(observe, "SshTarget", _StubNode)
    _StubNode.reachable = {"up": ["saage-run-1"]}
    observe.ps()
    out = capsys.readouterr().out
    assert "checking up (u@h1)" in out
    assert "checking down (h2)" in out
    assert "unreachable" in out


def test_sessions_raises_when_ssh_fails(saage_home, monkeypatch):
    # ssh exits 255 on connection failure; check=False must not turn a dead box
    # into "0 sessions" — ps/cleanup rely on the exception to say "unreachable"
    import subprocess
    from saage.remote.sshio import SSHError
    from saage.remote.target import SshTarget
    add_target("dead", "h1")
    from saage.remote.creds import get_target
    node = SshTarget(get_target("dead"))
    fake = subprocess.CompletedProcess(args=[], returncode=255, stdout="", stderr="")
    monkeypatch.setattr(type(node.conn), "run",
                        lambda self, *a, **k: fake)
    with pytest.raises(SSHError):
        node.sessions()


def test_sessions_empty_when_no_tmux_server(saage_home, monkeypatch):
    # a reachable box with no tmux server: tmux ls exits 1 — that IS 0 sessions
    import subprocess
    from saage.remote.target import SshTarget
    add_target("up", "h1")
    from saage.remote.creds import get_target
    node = SshTarget(get_target("up"))
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    monkeypatch.setattr(type(node.conn), "run",
                        lambda self, *a, **k: fake)
    assert node.sessions() == []


# -- list ----------------------------------------------------------------------

def test_list_prints_targets(saage_home, capsys):
    from saage.remote.cli import _dispatch
    add_target("spark", "spark.local", user="saage", hourly_usd=1.29)
    add_target("plain", "h2")
    _dispatch(argparse.Namespace(remote_command="list"))
    out = capsys.readouterr().out
    assert "spark" in out and "saage@spark.local" in out and "1.29" in out
    assert "plain" in out and "h2" in out


def test_list_no_targets(saage_home, capsys):
    from saage.remote.cli import _dispatch
    _dispatch(argparse.Namespace(remote_command="list"))
    assert "none registered" in capsys.readouterr().out


# -- terminate unregisters -----------------------------------------------------

class _FakeLambda:
    def __init__(self):
        self.terminated = []

    def instances(self):
        return [{"id": "i-1", "ip": "1.2.3.4", "status": "active"}]

    def terminate(self, ids):
        self.terminated += ids
        return [{"id": i} for i in ids]


def test_terminate_unregisters_target(saage_home, capsys, monkeypatch):
    from saage.remote import cli as rcli
    add_target("box", "1.2.3.4", user="ubuntu")
    api = _FakeLambda()
    monkeypatch.setattr(rcli, "_lambda_api", lambda: api)
    rcli._dispatch(argparse.Namespace(remote_command="terminate", target="box"))
    assert api.terminated == ["i-1"]
    assert "box" not in list_targets()


def test_terminate_by_ip_keeps_unrelated_targets(saage_home, monkeypatch):
    from saage.remote import cli as rcli
    add_target("other", "9.9.9.9")
    monkeypatch.setattr(rcli, "_lambda_api", lambda: _FakeLambda())
    rcli._dispatch(argparse.Namespace(remote_command="terminate", target="1.2.3.4"))
    assert set(list_targets()) == {"other"}
