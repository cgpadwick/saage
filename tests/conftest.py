"""Make `tests/` importable (so `saage_testkit` works from subdirs) + flow fixture."""
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
