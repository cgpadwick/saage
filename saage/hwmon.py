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
    """Current GPU utilization %, first GPU (raises on any problem)."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=utilization.gpu",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5)
    return int(float(out.stdout.strip().splitlines()[0]))


class HwSampler:
    """Sample GPU util + loadavg every `interval` seconds until stop().

    The first sample is taken immediately, so even a command shorter than one
    interval gets a reading.
    """

    def __init__(self, interval: float | None = None):
        if interval is None:
            interval = float(os.environ.get("SAAGE_HW_SAMPLE_SECS", "5"))
        self.interval = interval
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
        """Return {} when nothing could be sampled; JSON-safe floats otherwise."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        out: dict = {}
        if self._gpu:
            out["gpu_util_avg"] = round(sum(self._gpu) / len(self._gpu), 1)
            out["gpu_util_max"] = max(self._gpu)
        if self._load:
            out["load_avg"] = round(sum(self._load) / len(self._load), 2)
        if out:
            out["hw_samples"] = max(len(self._gpu), len(self._load))
        return out
