"""`saage run <flow.yaml>` — hydrate a flow and run it.

  saage run flows/story_writer/flow.yaml
  saage run flows/greenfield_ml/flow.yaml --workspace /tmp/ws --set target_accuracy=0.97
  OPENROUTER_API_KEY=... saage run f.yaml --provider openrouter --model "deepseek/deepseek-v4-flash"

See `saage run --help` for all options.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .hydrate import build_flow

# third-party libs whose INFO chatter (e.g. "HTTP Request: POST ...") is noise
_NOISY = ("httpx", "httpcore", "openai", "anthropic", "urllib3")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="saage", description="Run a saage workflow.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="hydrate and run a flow")
    run.add_argument("flow", metavar="flow.yaml", help="path to the flow YAML")
    run.add_argument("--workspace", metavar="DIR",
                     help="dir tools/commands operate in (default: the flow's dir)")
    run.add_argument("--venv", metavar="DIR",
                     help="virtualenv (relative to workspace) auto-activated for "
                          "commands once it exists (default: .venv)")
    run.add_argument("--provider", help="override the flow's provider type")
    run.add_argument("--model", help="override the model")
    run.add_argument("--base-url", dest="base_url", help="override the provider base URL")
    run.add_argument("--config", metavar="engine.yaml",
                     help="engine config YAML tuning the run_command safety policy "
                          "(default: the built-in denylist)")
    run.add_argument("--set", dest="overrides", metavar="KEY=VALUE", action="append",
                     default=[], help="seed/override a shared-store value (repeatable; "
                                      "value is parsed as JSON when possible)")
    verbosity = run.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true",
                           help="show tool-output detail (DEBUG) + the full results")
    verbosity.add_argument("-q", "--quiet", action="store_true",
                           help="quiet progress logs (WARNING+ only)")
    return parser


def _setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)


def _parse_set(items: list[str]) -> dict:
    """Turn ['k=v', ...] into {'k': v}, parsing each value as JSON when possible."""
    shared: dict = {}
    for item in items:
        key, _, value = item.partition("=")
        try:
            value = json.loads(value)            # numbers / bools / null / json
        except json.JSONDecodeError:
            pass                                 # leave as a plain string
        shared[key] = value
    return shared


# Directories the end-of-run summary's "files written" line skips, so it shows real
# outputs instead of hundreds of env/tooling/data internals. This is purely a DISPLAY
# filter for the summary — it is NOT a .gitignore and is never written to disk; it just
# happens to overlap the usual ignore patterns because the same dirs are noise either way.
_SUMMARY_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                      "node_modules", "data", ".mypy_cache", ".ruff_cache"}


def _snapshot(root: Path) -> dict:
    snap = {}
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix in (".pyc", ".pyo"):
            continue
        if _SUMMARY_SKIP_DIRS & set(p.relative_to(root).parts):
            continue
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
    args = _build_parser().parse_args(argv)
    _setup_logging(args.verbose, args.quiet)

    overrides = {"type": args.provider, "model": args.model, "base_url": args.base_url}
    flow, seed = build_flow(args.flow, provider_overrides=overrides,
                            workspace=args.workspace, venv=args.venv, config=args.config)
    seed.update(_parse_set(args.overrides))
    root = Path(seed["workspace"])               # the resolved workspace

    before = _snapshot(root)
    log = logging.getLogger("saage")
    log.info("starting run")
    flow.run(seed)
    log.info("run complete")
    after = _snapshot(root)

    _print_summary(seed, before, after, root)
    if args.verbose:                              # full agent/command outputs
        print("\nresults:")
        print(json.dumps(seed.get("results", {}), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
