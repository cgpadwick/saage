# tests/test_cli_resume.py
"""CLI: `saage run` creates a checkpoint; `runs`/`resume` use the registry."""
import pytest

from saage import checkpoint as ckpt
from saage.cli import main


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / ".saage"))


def _command_flow(tmp_path):
    f = tmp_path / "flow.yaml"
    f.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - {id: only, type: command, run: 'echo hi'}\n"
    )
    return f


def test_run_creates_completed_checkpoint(tmp_path):
    f = _command_flow(tmp_path)
    rc = main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    assert rc == 0
    runs = ckpt.list_runs()
    assert len(runs) == 1
    assert runs[0].load()["status"] == "completed"


def test_run_marks_failed_on_engine_error(tmp_path, monkeypatch):
    import saage.nodes as nodes
    f = _command_flow(tmp_path)

    def boom(cmd, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(nodes, "run_shell", boom)
    with pytest.raises(RuntimeError, match="kaboom"):
        main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    runs = ckpt.list_runs()
    assert len(runs) == 1
    assert runs[0].load()["status"] == "failed"


def test_runs_lists_runs(tmp_path, capsys):
    f = _command_flow(tmp_path)
    main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    rc = main(["runs"])
    assert rc == 0
    out = capsys.readouterr().out
    runs = ckpt.list_runs()
    assert runs[0].run_id in out
    assert "completed" in out


def test_resume_refuses_on_fingerprint_mismatch(tmp_path, monkeypatch, capsys):
    import saage.nodes as nodes
    from saage.shell import run_shell as real_shell
    f = _command_flow(tmp_path)

    # make the run fail so it stays resumable
    def boom(cmd, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(nodes, "run_shell", boom)
    with pytest.raises(RuntimeError):
        main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    monkeypatch.setattr(nodes, "run_shell", real_shell)

    # edit the flow so the fingerprint no longer matches
    f.write_text(f.read_text() + "\n# edited\n")
    rc = main(["resume"])
    assert rc == 1                       # refused


def test_resume_completes_a_crashed_run(tmp_path, monkeypatch):
    import saage.nodes as nodes
    from saage.shell import run_shell as real_shell
    # a 2-step command flow; crash on the 2nd step, then resume to finish it
    f = tmp_path / "flow.yaml"
    f.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - {id: one, type: command, run: 'echo 1 >> log.txt'}\n"
        "  - {id: two, type: command, run: 'echo 2 >> log.txt'}\n"
    )
    log = tmp_path / "log.txt"
    calls = {"n": 0}

    def flaky(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real_shell(cmd, **kw)

    monkeypatch.setattr(nodes, "run_shell", flaky)
    with pytest.raises(RuntimeError):
        main(["run", str(f), "--workspace", str(tmp_path), "-q"])
    assert log.read_text().strip() == "1"

    monkeypatch.setattr(nodes, "run_shell", real_shell)
    rc = main(["resume", "-q"])
    assert rc == 0
    # step one not redone; step two completed -> exactly "1\n2"
    assert log.read_text().split() == ["1", "2"]
    # the resumed run is now marked completed (not left "running")
    runs = ckpt.list_runs()
    assert runs[0].load()["status"] == "completed"
