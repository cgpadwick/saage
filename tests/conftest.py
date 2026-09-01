"""Make `tests/` importable (so `saage_testkit` works from subdirs) + flow fixture."""
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

FLOWS = Path(__file__).resolve().parent.parent / "flows"


@pytest.fixture(autouse=True)
def _no_ambient_saage_shell(monkeypatch):
    """An exported SAAGE_SHELL (e.g. =cmd) must not leak into the suite — the
    dialect tests would fail confusingly and the venv test would re-create the
    interactive-REPL hang. Tests that exercise the override set it themselves."""
    from saage.shell import find_bash
    monkeypatch.delenv("SAAGE_SHELL", raising=False)
    find_bash.cache_clear()
    yield
    find_bash.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_saage_home(tmp_path, monkeypatch):
    """Redirect SAAGE_HOME to a temp dir for every test, so run checkpoints
    (and any other ~/.saage state) never leak into the developer's real home.
    Tests that need their own SAAGE_HOME (e.g. to share one across steps) set it
    themselves and override this."""
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / ".saage"))


@pytest.fixture
def flow_copy(tmp_path):
    """Copy a flow fixture into a fresh temp dir so runs are hermetic (helper
    scripts create artifacts next to the flow). Safe to call multiple times."""
    counter = {"n": 0}
    # never copy run artifacts a prior in-place `saage run` may have left behind
    ignore = shutil.ignore_patterns(
        "story.md", "review.md", "history.txt", "job_*.count",
        "__pycache__", "*.pyc")

    def _copy(name: str) -> Path:
        dst = tmp_path / f"{name}_{counter['n']}"
        counter["n"] += 1
        shutil.copytree(FLOWS / name, dst, ignore=ignore)
        return dst / "flow.yaml"
    return _copy


@pytest.fixture
def blocked_stdin():
    """Make fd 0 a pipe whose writer is held open for the test: any child that
    reads stdin blocks forever unless the engine handed it EOF (/dev/null).
    Windows: os.dup2 on CRT fd 0 does not touch the Win32 STD_INPUT_HANDLE
    that subprocess inherits, so the fixture would be a silent no-op there."""
    if sys.platform == "win32":
        pytest.skip("fd-0 dup2 does not reach the Win32 stdin handle")
    r, w = os.pipe()
    saved = os.dup(0)
    os.dup2(r, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        for fd in (r, w, saved):
            os.close(fd)
