"""Filesystem locations for saage run state under ~/.saage.

One definition, shared by the engine's checkpoint store (saage.checkpoint) and
the remote subsystem (saage.remote.*), so "where runs live" is not duplicated.
SAAGE_HOME relocates the root (used by tests).
"""
from __future__ import annotations

import os
from pathlib import Path


def saage_home() -> Path:
    return Path(os.environ.get("SAAGE_HOME", "~/.saage")).expanduser()


def runs_dir() -> Path:
    return saage_home() / "runs"
