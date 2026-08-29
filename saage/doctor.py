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

    # the ✓/✗/- glyphs must never crash a legacy-codepage (cp1252) console;
    # doctor runs before cli._setup_logging's equivalent guard
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

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

    print("provider defaults (saage setup)")
    from .settings import config_path, default_provider, stored_key
    dp = default_provider()
    if dp:
        _ok(f"default provider: {dp.get('type')} / {dp.get('model')} "
            f"({config_path()})")
    else:
        _warn(f"no {config_path()} — flows that don't pin a provider need "
              f"`saage setup` (or --provider/--model on each run)")

    print("provider API keys (any one is enough to run the example flows)")
    have_key = False
    for ptype, env in _KEYS:
        if os.environ.get(env):
            _ok(f"{env} set in the environment ({ptype})")
            have_key = True
        elif stored_key(env):
            _ok(f"{env} saved in credentials.toml ({ptype})")
            have_key = True
        else:
            _warn(f"{env} not set ({ptype})")
    if not have_key:
        _warn("no provider key configured — agent flows will refuse to "
              "start; run `saage setup`, export a key, or use a local model "
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

    def _flow_files(base: Path) -> list[Path]:
        # pathlib's `*` matches dotdirs too — skip hidden/tooling dirs so a
        # stray .venv/flow.yaml or node_modules copy is never "diagnosed"
        junk = {"node_modules", "__pycache__"}
        return sorted(p for p in base.glob("*/flow.yaml")
                      if not p.parent.name.startswith(".")
                      and p.parent.name not in junk)

    flow_files = _flow_files(Path("flows")) or _flow_files(Path("."))
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
