"""Step metrics: every command step leaves compact, JSON-safe evidence.

`step_metrics.<id>` in the shared store carries exit + wall_seconds always, a
bounded stderr_tail on failure, and (with `measure_hw: true`) GPU/loadavg
aggregates. This is the evidence stream that lets verifier prompts reason from
the real error instead of the exit code, and lets a proposer see what an
experiment *costs* — the two blind spots behind the kaggle run's phantom-OOM
misdiagnoses and 22-hour single-threaded trains.
"""
import json
import os

import pytest

from saage.hwmon import HwSampler
from saage.hydrate import build_flow
from saage.nodes import CommandNode, render

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX shell timings")


def _run(node):
    shared: dict = {}
    node.run(shared)
    return shared


# ------------------------------------------------------------- wall + exit --

def test_wall_and_exit_recorded_on_success(tmp_path):
    shared = _run(CommandNode("t", "sleep 0.3; echo ok", tmp_path))
    m = shared["step_metrics"]["t"]
    assert m["exit"] == 0
    assert m["wall_seconds"] >= 0.3
    assert "stderr_tail" not in m            # success carries no failure tail


def test_results_dict_shape_unchanged(tmp_path):
    # the metrics record must NOT leak into results — prompts template results
    # for stdout, step_metrics for evidence; keep the shapes stable
    shared = _run(CommandNode("t", "echo ok", tmp_path))
    assert set(shared["results"]["t"]) == {"exit", "stdout", "stderr"}


# --------------------------------------------------------- failure evidence --

def test_stderr_tail_on_failure(tmp_path):
    shared = _run(CommandNode("t", "echo 'ValueError: boom' >&2; exit 1", tmp_path))
    m = shared["step_metrics"]["t"]
    assert m["exit"] == 1
    assert "ValueError: boom" in m["stderr_tail"]


def test_tail_falls_back_to_stdout(tmp_path):
    # tracebacks that land on stdout (pytest, some launchers) still get kept
    shared = _run(CommandNode("t", "echo 'AssertionError: nope'; exit 2", tmp_path))
    assert "AssertionError: nope" in shared["step_metrics"]["t"]["stderr_tail"]


def test_tail_is_bounded(tmp_path):
    shared = _run(CommandNode(
        "t", "python3 -c \"import sys; sys.stderr.write('x'*100000); sys.exit(1)\"",
        tmp_path))
    assert len(shared["step_metrics"]["t"]["stderr_tail"]) <= 2000


@posix_only
def test_timeout_records_metrics_too(tmp_path):
    shared = _run(CommandNode("t", "sleep 30", tmp_path, timeout=0.3))
    m = shared["step_metrics"]["t"]
    assert m["exit"] == 124
    assert 0.3 <= m["wall_seconds"] < 5
    assert "timed out" in m["stderr_tail"]


# ------------------------------------------------------------------ measure --

def test_measure_hw_records_load_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HW_SAMPLE_SECS", "0.05")
    shared = _run(CommandNode("t", "sleep 0.4", tmp_path, measure_hw=True))
    m = shared["step_metrics"]["t"]
    if hasattr(os, "getloadavg"):            # POSIX: load must be there
        assert m["load_avg"] >= 0
        assert m["hw_samples"] >= 1
    # GPU keys appear only on GPU boxes — absence is not a failure


def test_measure_hw_never_breaks_the_step(tmp_path, monkeypatch):
    # even with nvidia-smi absent / failing, the command's own result stands
    monkeypatch.setenv("SAAGE_HW_SAMPLE_SECS", "0.05")
    monkeypatch.setenv("PATH", str(tmp_path))     # nothing on PATH at all
    shared = _run(CommandNode("t", "echo fine", tmp_path, measure_hw=True))
    assert shared["results"]["t"]["exit"] == 0


def test_hwsampler_gpu_failure_stops_gpu_sampling(monkeypatch):
    s = HwSampler(interval=0.01)
    calls = []
    def boom():
        calls.append(1)
        raise RuntimeError("no gpu")
    monkeypatch.setattr("saage.hwmon._gpu_util", boom)
    s.start()
    import time
    time.sleep(0.1)
    s.stop()
    assert len(calls) == 1                   # failed once, never retried


# --------------------------------------------------- serialization + jinja --

def test_metrics_are_json_serializable(tmp_path):
    # resumability rides on the shared store staying JSON-safe (CLAUDE.md)
    shared = _run(CommandNode("t", "echo ok", tmp_path))
    json.dumps(shared["step_metrics"])


def test_metrics_are_templatable(tmp_path):
    shared = _run(CommandNode("train", "echo ok", tmp_path))
    text = render("took {{ step_metrics.train.wall_seconds }}s "
                  "exit {{ step_metrics.train.exit }}", shared)
    assert "exit 0" in text


# ------------------------------------------------------------------ hydrate --

def _flow(tmp_path, extra: str) -> str:
    f = tmp_path / "flow.yaml"
    f.write_text(
        "workflow:\n"
        f"  - {{ id: t, type: command, run: 'echo ok'{extra} }}\n",
        encoding="utf-8")
    return str(f)


def test_hydrate_accepts_measure_hw(tmp_path):
    build_flow(_flow(tmp_path, ", measure_hw: true"), provider=object(),
               workspace=str(tmp_path))
    build_flow(_flow(tmp_path, ", measure_hw: false"), provider=object(),
               workspace=str(tmp_path))


def test_hydrate_rejects_non_bool_measure_hw(tmp_path):
    with pytest.raises(ValueError, match="measure_hw must be true/false"):
        build_flow(_flow(tmp_path, ", measure_hw: 'yes'"), provider=object(),
                   workspace=str(tmp_path))
