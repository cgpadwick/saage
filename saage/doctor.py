"""`saage doctor` — check the local setup and say exactly what's missing.

Prints one line per check; returns the number of hard problems found (0 = all
good). Informational misses (an optional key not set) don't count as problems.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from .paths import saage_home

# provider type -> key env var (kept in sync with saage.hydrate.make_provider)
_KEYS = [
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("nvidia", "NVIDIA_API_KEY"),
]


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _warn(msg: str) -> None:
    print(f"  - {msg}")


def _bad(msg: str) -> None:
    print(f"  ✗ {msg}")


def run_doctor() -> int:
    problems = 0

    print("environment")
    v = sys.version_info
    if v >= (3, 10):
        _ok(f"python {v.major}.{v.minor}.{v.micro}")
    else:
        _bad(f"python {v.major}.{v.minor} — saage needs ≥ 3.10")
        problems += 1
    try:
        from .shell import find_bash
        _ok(f"bash: {find_bash()}")
    except Exception as e:  # noqa: BLE001 — any failure here is the finding itself
        _bad(f"bash not found ({e}) — command steps cannot run")
        problems += 1

    print("provider API keys (any one is enough to run the example flows)")
    have_key = False
    for ptype, env in _KEYS:
        if os.environ.get(env):
            _ok(f"{env} set ({ptype})")
            have_key = True
        else:
            _warn(f"{env} not set ({ptype})")
    if not have_key:
        _warn("no provider key in the environment — agent flows will refuse "
              "to start; export one, or use a local model "
              "(--provider local --base-url http://localhost:11434/v1)")

    print("web UI")
    if importlib.util.find_spec("fastapi") and importlib.util.find_spec("uvicorn"):
        _ok("server extra installed (saage serve available)")
    else:
        _warn("server extra not installed — pip install 'saage[server]' "
              "for the web UI")
    server_yaml = saage_home() / "server.yaml"
    if server_yaml.is_file():
        _ok(f"server config: {server_yaml}")
    else:
        _warn(f"no {server_yaml} — saage serve uses defaults and "
              f"auto-discovers ./flows")

    print("flows")
    flow_files = sorted(Path("flows").glob("*/flow.yaml")) or \
        sorted(Path(".").glob("*/flow.yaml"))
    if not flow_files:
        _warn("no */flow.yaml found under ./flows or . — "
              "scaffold one with: saage new my_flow")
    else:
        import tempfile

        from .hydrate import build_flow
        for fy in flow_files:
            try:
                build_flow(fy, provider=object(),
                           workspace=tempfile.mkdtemp(prefix="saage-doctor-"))
                _ok(f"{fy.parent.name}: hydrates")
            except Exception as e:  # noqa: BLE001 — report, keep checking the rest
                _bad(f"{fy.parent.name}: {e}")
                problems += 1

    print("ok — no problems found" if problems == 0
          else f"{problems} problem(s) found")
    return problems
