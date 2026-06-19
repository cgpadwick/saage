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
