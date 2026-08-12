"""Hardware sampling for `measure_hw:` command steps.

A background thread samples GPU utilization (`nvidia-smi`) and the 1-minute
load average while the command runs; `stop()` returns compact aggregates. The
point is to make hardware misfit *observable* in the run's evidence stream: a
"successful" train step that shows 2% GPU on a GPU box, or one core busy on a
30-core box, is a performance bug that a score-only ledger can never surface.

Everything is best-effort and must never break the step: no GPU → no GPU keys;
no getloadavg (Windows) → no load key; nvidia-smi failing once stops further
GPU attempts for this sampler.
"""
from __future__ import annotations

import os
import subprocess
import threading


def _gpu_util() -> int:
    """Current GPU utilization %: the MAX across all GPUs (raises on any
    problem). Max, not first-GPU: on a multi-GPU box the job may be pinned to
    any device (CUDA_VISIBLE_DEVICES=1), and the question this metric answers
    is "is ANY GPU doing the work" — reporting an idle GPU 0 while GPU 1
    trains would be confidently wrong evidence."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=utilization.gpu",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5)
    values = [int(float(line)) for line in out.stdout.strip().splitlines()
              if line.strip()]
    if not values:
        raise ValueError("nvidia-smi returned no utilization values")
    return max(values)


class HwSampler:
    """Sample GPU util + loadavg every `interval` seconds until stop().

    The first sample is taken immediately, so even a command shorter than one
    interval gets a reading.
    """

    _DEFAULT_INTERVAL = 5.0
    _MIN_INTERVAL = 0.05          # floor: never busy-loop nvidia-smi

    def __init__(self, interval: float | None = None):
        if interval is None:
            raw = os.environ.get("SAAGE_HW_SAMPLE_SECS", "")
            try:
                interval = float(raw) if raw else self._DEFAULT_INTERVAL
            except ValueError:    # a typo'd env var must never break the step
                interval = self._DEFAULT_INTERVAL
        if not (interval > 0):    # rejects 0, negatives, and NaN
            interval = self._DEFAULT_INTERVAL
        self.interval = max(interval, self._MIN_INTERVAL)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu: list[int] = []
        self._load: list[float] = []
        self._gpu_ok = True

    def _sample(self) -> None:
        if self._gpu_ok:
            try:
                self._gpu.append(_gpu_util())
            except Exception:            # no nvidia-smi / no GPU: stop trying
                self._gpu_ok = False
        if hasattr(os, "getloadavg"):    # POSIX only
            try:
                self._load.append(os.getloadavg()[0])
            except OSError:
                pass

    def _loop(self) -> None:
        while True:
            self._sample()
            if self._stop.wait(self.interval):
                return

    def start(self) -> "HwSampler":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict:
        """Return {} when nothing could be sampled; JSON-safe floats otherwise.

        The join is short (a wedged nvidia-smi holds a sample for up to its
        5s subprocess timeout; the step must not inherit that stall), and the
        lists are SNAPSHOTTED before aggregating — an in-flight sample may
        still append after an expired join, and sum/len/max must agree."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        gpu = list(self._gpu)     # consistent snapshots (list() is atomic)
        load = list(self._load)
        out: dict = {}
        if gpu:
            out["gpu_util_avg"] = round(sum(gpu) / len(gpu), 1)
            out["gpu_util_max"] = max(gpu)
        if load:
            out["load_avg"] = round(sum(load) / len(load), 2)
        if out:
            out["hw_samples"] = max(len(gpu), len(load))
        return out
