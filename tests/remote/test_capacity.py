"""H1: per-target capacity (max_runs slots) — registry roundtrip and the
preflight guard, offline (transport monkeypatched)."""
from __future__ import annotations

import pytest

from saage.remote.creds import Target, add_target, get_target
from saage.remote.sshio import SSHConn
from saage.remote.target import PreflightError, SshTarget


def test_max_runs_roundtrip(saage_home):
    add_target("packed", "h1", max_runs=4)
    assert get_target("packed").max_runs == 4


def test_max_runs_default_is_one(saage_home):
    add_target("solo", "h1")
    assert get_target("solo").max_runs == 1


def test_max_runs_clamped_to_at_least_one(saage_home, tmp_path):
    add_target("weird", "h1")
    path = saage_home / "credentials.toml"
    path.write_text(path.read_text().replace('host = "h1"',
                                             'host = "h1"\nmax_runs = 0'))
    assert get_target("weird").max_runs == 1


@pytest.fixture
def quiet_transport(monkeypatch):
    """ssh always reachable, every tool present, no GPU complaints."""
    monkeypatch.setattr(SSHConn, "run", lambda self, *a, **k: type(
        "P", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(SSHConn, "ok", lambda self, *a, **k: True)


def _node(max_runs: int, live_sessions: list[str], monkeypatch) -> SshTarget:
    node = SshTarget(Target(name="box", host="h", max_runs=max_runs))
    monkeypatch.setattr(SshTarget, "sessions", lambda self: list(live_sessions))
    return node


def test_preflight_rejects_at_capacity_default(quiet_transport, monkeypatch):
    node = _node(1, ["saage-r1"], monkeypatch)
    with pytest.raises(PreflightError, match="at capacity"):
        node.preflight()


def test_preflight_allows_below_capacity(quiet_transport, monkeypatch):
    node = _node(3, ["saage-r1", "saage-r2"], monkeypatch)
    assert node.preflight() == []


def test_preflight_rejects_full_multislot_box(quiet_transport, monkeypatch):
    node = _node(2, ["saage-r1", "saage-r2"], monkeypatch)
    with pytest.raises(PreflightError, match="2 concurrent runs"):
        node.preflight()


def test_free_slots(monkeypatch):
    node = _node(3, ["saage-r1"], monkeypatch)
    assert node.free_slots() == 2
    node = _node(1, ["saage-r1", "saage-orphan"], monkeypatch)
    assert node.free_slots() == 0
