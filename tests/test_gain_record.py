"""gain_record helper: computes specialized-minus-paper gain, logs it."""
import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "contrib/lewm_hillclimb_guided/gain_record.py"


def _run(tmp_path, *args):
    return subprocess.run([sys.executable, str(_PATH), *args],
                          cwd=tmp_path, capture_output=True, text=True)


def test_prints_gain_and_writes_log(tmp_path):
    r = _run(tmp_path, "--specialized", "82.0", "--paper", "76.0", "--dino", "86.0")
    assert r.returncode == 0
    assert "GAIN=6.0" in r.stdout
    log = (tmp_path / "research_log.md").read_text()
    assert "82" in log and "76" in log and "86" in log
    assert "6.0" in log


def test_negative_gain_is_reported_not_clamped(tmp_path):
    r = _run(tmp_path, "--specialized", "74.0", "--paper", "76.0", "--dino", "86.0")
    assert "GAIN=-2.0" in r.stdout


def test_failure_sentinel_paper_does_not_crash(tmp_path):
    # a crashed paper headline reads -1; the helper must still exit 0
    r = _run(tmp_path, "--specialized", "80.0", "--paper", "-1", "--dino", "86.0")
    assert r.returncode == 0
    assert "GAIN=" in r.stdout
