"""prepare_comp.py device detection — must not crash on a host without
nvidia-smi (CI, CPU-only boxes). Regression: subprocess.run(['nvidia-smi'])
raises FileNotFoundError when the binary is absent, which crashed prepare_comp
before it printed SAMPLE_COLS=..., emptying every downstream capture and
failing the integration test only in CI (the dev box had a GPU)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "prepare_comp",
    Path(__file__).resolve().parents[1] / "flows" / "kaggle_solver" / "prepare_comp.py")
prepare_comp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare_comp)


def test_force_short_circuits_detection():
    assert prepare_comp.detect_device("cpu") == "cpu"
    assert prepare_comp.detect_device("cuda") == "cuda"


def test_missing_nvidia_smi_returns_cpu(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "nvidia-smi")
    monkeypatch.setattr(prepare_comp.subprocess, "run", boom)
    assert prepare_comp.detect_device("") == "cpu"     # no crash, falls back to cpu


def test_nvidia_smi_present_returns_cuda(monkeypatch):
    import subprocess
    monkeypatch.setattr(prepare_comp.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b""))
    assert prepare_comp.detect_device("") == "cuda"
