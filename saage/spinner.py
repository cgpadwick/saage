"""A tiny braille spinner shown while a (blocking) model call is in flight.

Animates on a real terminal only; in piped / non-TTY / quiet contexts it is a
no-op, so logs and tests stay clean. The label is a rotating, mildly whimsical
gerund so you can tell calls apart at a glance.
"""
from __future__ import annotations

import itertools
import logging
import random
import sys
import threading
import time

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_WORDS = [
    "calling the LLM", "computing", "cogitating", "ruminating", "percolating",
    "conjuring tokens", "divining", "noodling", "incanting", "marinating",
    "consulting the oracle", "flibbertigibbeting", "bamboozling", "galavanting",
    "harmonizing", "effervescing", "pondering the imponderables",
]


class Spinner:
    def __init__(self, label: str | None = None, stream=None, interval: float = 0.09):
        self.label = label or random.choice(_WORDS)
        self.stream = stream or sys.stderr
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # only animate on an interactive terminal when progress logging is on
        self.enabled = (
            self.stream.isatty()
            and logging.getLogger("saage").getEffectiveLevel() <= logging.INFO
        )

    def __enter__(self):
        if self.enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def _spin(self):
        for frame in itertools.cycle(_FRAMES):
            if self._stop.is_set():
                break
            self.stream.write(f"\r    {frame} {self.label}… ")
            self.stream.flush()
            time.sleep(self.interval)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join()
        if self.enabled:
            self.stream.write("\r\033[K")   # carriage-return + clear-to-EOL
            self.stream.flush()
