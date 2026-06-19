# tests/integration/test_resume.py
"""Crash mid-loop, then resume — completed iterations are not redone."""
import pytest

from saage import checkpoint as ckpt
from saage.hydrate import build_flow


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / ".saage"))


def _loop_flow(tmp_path):
    f = tmp_path / "flow.yaml"
    f.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - id: hill\n"
        "    type: counting_loop\n"
        "    max_iterations: 5\n"
        "    body:\n"
        "      - {id: tick, type: command, run: 'echo x >> counter.txt'}\n"
    )
    return f


def test_checkpoint_written_during_run(tmp_path):
    f = _loop_flow(tmp_path)
    c = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(f),
                               workspace=str(tmp_path))
    flow, seed = build_flow(f, provider=object(), workspace=str(tmp_path),
                            checkpoint=c)
    flow.run(seed)
    rec = c.load()
    # the loop is the only (index 0) top-level step
    assert rec["resume_step"] == 0
    assert rec["shared"]["_iter"]["hill"] == 5
