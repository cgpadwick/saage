"""cwe run <flow.yaml> [--config engine.yaml] [--provider T] [--model M] [--base-url U] [--set k=v ...] [-v|-q]

--provider / --model / --base-url override the flow's provider block, so you can
run the same flow against a different backend/model without editing the YAML, e.g.

    OPENROUTER_API_KEY=... cwe run flows/story_writer/flow.yaml \\
        --provider openrouter --model "anthropic/claude-3.5-sonnet"

--config points at an engine config YAML that tunes the run_command safety policy
(the denied-command patterns). Omit it for the safe built-in denylist.

-v shows tool-output detail (DEBUG); -q quiets progress logs (WARNING+ only).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .hydrate import run_flow

USAGE = ("usage: cwe run <flow.yaml> [--config engine.yaml] [--provider T] "
         "[--model M] [--base-url U] [--set key=value ...] [-v|-q]")

# third-party libs whose INFO chatter (e.g. "HTTP Request: POST ...") is noise
_NOISY = ("httpx", "httpcore", "openai", "anthropic", "urllib3")


def _setup_logging(argv: list[str]) -> None:
    level = logging.INFO
    if "-v" in argv or "--verbose" in argv:
        level = logging.DEBUG
    elif "-q" in argv or "--quiet" in argv:
        level = logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)


def _parse(args: list[str]) -> tuple[dict, dict, str | None]:
    overrides: dict = {"type": None, "model": None, "base_url": None}
    shared: dict = {}
    config: str | None = None
    flag = {"--provider": "type", "--model": "model", "--base-url": "base_url"}
    i = 0
    while i < len(args):
        a = args[i]
        if a in flag and i + 1 < len(args):
            overrides[flag[a]] = args[i + 1]
            i += 2
        elif a == "--config" and i + 1 < len(args):
            config = args[i + 1]
            i += 2
        elif a == "--set" and i + 1 < len(args):
            key, _, value = args[i + 1].partition("=")
            try:
                value = json.loads(value)        # allow numbers/bools/json
            except json.JSONDecodeError:
                pass
            shared[key] = value
            i += 2
        else:                                    # -v/-q and unknown flags
            i += 1
    return overrides, shared, config


def _snapshot(root: Path) -> dict:
    snap = {}
    for p in root.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            try:
                snap[p] = p.stat().st_mtime
            except OSError:
                pass
    return snap


def _collapse(trace: list) -> str:
    counts: dict = {}
    for x in trace:
        counts[x] = counts.get(x, 0) + 1
    return ", ".join(f"{k} ×{v}" if v > 1 else k for k, v in counts.items())


def _print_summary(result: dict, before: dict, after: dict, root: Path) -> None:
    changed = sorted(p.relative_to(root).as_posix()
                     for p in after if after[p] != before.get(p))
    print("\n── run summary ─────────────────────────────────")
    trace = result.get("_trace", [])
    if trace:
        print(f"  steps:  {_collapse(trace)}")
    for name, reason in result.get("_exit_reason", {}).items():
        n = result.get("_iter", {}).get(name)
        print(f"  loop:   {name} → {n} iteration(s) ({reason})")
    print(f"  files:  {', '.join(changed) if changed else '(none changed)'}")
    print("────────────────────────────────────────────────")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "run":
        print(USAGE, file=sys.stderr)
        return 2
    _setup_logging(argv)
    overrides, shared, config = _parse(argv[2:])
    root = Path(argv[1]).parent

    before = _snapshot(root)
    result = run_flow(argv[1], shared=shared, provider_overrides=overrides,
                      config=config)
    after = _snapshot(root)

    _print_summary(result, before, after, root)
    if "-v" in argv or "--verbose" in argv:        # full agent/command outputs
        print("\nresults:")
        print(json.dumps(result.get("results", {}), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
