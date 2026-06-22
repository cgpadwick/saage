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
import os
import sys
from pathlib import Path

from .hydrate import build_flow
from . import checkpoint as ckpt

# third-party libs whose INFO chatter (e.g. "HTTP Request: POST ...") is noise
_NOISY = ("httpx", "httpcore", "openai", "anthropic", "urllib3")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="saage", description="Run a saage workflow.")
    sub = parser.add_subparsers(dest="command", required=True)
    from .remote.cli import add_parser as _add_remote_parser
    _add_remote_parser(sub)
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

    res = sub.add_parser("resume", help="resume a killed/crashed run")
    res.add_argument("run_id", nargs="?",
                     help="run id or unique prefix (default: latest resumable)")
    res.add_argument("--force", action="store_true",
                     help="resume even if the flow changed since the checkpoint")
    res.add_argument("--workspace", metavar="DIR",
                     help="override the recorded workspace")
    rv = res.add_mutually_exclusive_group()
    rv.add_argument("-v", "--verbose", action="store_true")
    rv.add_argument("-q", "--quiet", action="store_true")

    sub.add_parser("runs", help="list resumable runs")

    return parser


def _setup_logging(verbose: bool, quiet: bool) -> None:
    if os.name == "nt":
        # the engine's log glyphs (▶ ✓ ⚙ ↻) must never crash a run when output
        # is redirected to a legacy-codepage (cp1252) stream
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)


def _attach_run_log(run_dir: Path) -> None:
    """Tee the run's logs to <run_dir>/run.log so a local run leaves a persistent
    log file (parity with remote runs). Inherits the configured console level
    (-v/-q), so the file mirrors what the console shows.

    main()/resume can be called repeatedly in one process (tests, embedding), so
    first drop+close any run-log handler a previous run attached — otherwise logs
    duplicate across files and file descriptors leak."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_saage_run_log", False):
            root.removeHandler(h)
            h.close()
    fh = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    fh._saage_run_log = True
    fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(fh)


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


def _print_summary(result: dict, before: dict, after: dict, root: Path,
                   run_dir: Path | None = None) -> None:
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
    from .llm import USAGE
    if USAGE.calls:
        print(f"  tokens: {USAGE.total_tokens:,} ({USAGE.prompt_tokens:,} in + "
              f"{USAGE.completion_tokens:,} out) over {USAGE.calls:,} model call(s)")
    if run_dir is not None:
        print(f"  run dir: {run_dir}  (run.log · ledger.jsonl · shared.json)")
    print("────────────────────────────────────────────────")


def _position(rec: dict) -> str:
    step = rec.get("resume_step")
    if step is None:
        return "-"
    iters = rec.get("shared", {}).get("_iter", {})
    return f"step {step}" + (f", loop iter {max(iters.values())}" if iters else "")


def _cmd_runs() -> int:
    runs = ckpt.list_runs()
    if not runs:
        print("no runs recorded")
        return 0
    print(f"{'RUN ID':<24} {'STATUS':<10} {'POSITION':<18} FLOW")
    for r in runs:
        rec = ckpt._safe_load(r)
        if rec is None:
            continue
        print(f"{r.run_id:<24} {rec.get('status',''):<10} "
              f"{_position(rec):<18} {rec.get('flow_path','')}")
    return 0


def _cmd_resume(args) -> int:
    from .hydrate import run_flow
    log = logging.getLogger("saage")
    try:
        run = ckpt.find_run(args.run_id)
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1
    rec = run.load()
    if rec.get("status") == "completed" and not args.force:
        log.error("run %s already completed — nothing to resume "
                  "(use --force to re-run from its last step)", run.run_id)
        return 1
    flow_path = rec["flow_path"]
    if not Path(flow_path).is_file():
        log.error("flow file is gone: %s", flow_path)
        return 1
    current_fp = ckpt.fingerprint(flow_path)
    if rec.get("fingerprint") and current_fp != rec["fingerprint"] and not args.force:
        log.error("flow changed since checkpoint (%s); re-run fresh, or "
                  "`saage resume %s --force` to override", flow_path, run.run_id)
        return 1
    workspace = args.workspace or rec.get("workspace") or rec.get("shared", {}).get("workspace")
    _attach_run_log(run.dir)                      # append to the same run.log on resume
    log.info("resuming %s (run dir: %s)", run.run_id, run.dir)
    # run_flow marks the run failed if it raises; the engine stamps the terminal
    # completed/failed status into the final checkpoint write on a clean finish.
    run_flow(flow_path,
             provider_overrides=rec.get("provider_overrides") or None,
             workspace=workspace, venv=rec.get("venv"),
             config=rec.get("config_path"), resume=run)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "remote":
        _setup_logging(verbose=False, quiet=False)
        from .remote.cli import dispatch
        return dispatch(args)
    if args.command == "runs":
        _setup_logging(verbose=False, quiet=False)
        return _cmd_runs()
    if args.command == "resume":
        _setup_logging(args.verbose, args.quiet)
        return _cmd_resume(args)
    _setup_logging(args.verbose, args.quiet)

    overrides = {"type": args.provider, "model": args.model, "base_url": args.base_url}
    run_id = ckpt.new_run_id()
    flow_path = str(Path(args.flow).resolve())
    run = ckpt.Checkpoint.create(
        run_id,
        flow_path=flow_path,
        fingerprint=ckpt.fingerprint(flow_path),
        provider_overrides={k: v for k, v in overrides.items() if v is not None},
        config_path=str(Path(args.config).resolve()) if args.config else None,
        venv=args.venv,
    )
    _attach_run_log(run.dir)                      # local run dir gets run.log too
    logging.getLogger("saage").info("run dir: %s", run.dir)
    flow, seed = build_flow(args.flow, provider_overrides=overrides,
                            workspace=args.workspace, venv=args.venv,
                            config=args.config, checkpoint=run)
    seed.update(_parse_set(args.overrides))
    root = Path(seed["workspace"])               # the resolved workspace
    run.write(seed, resume_step=None, status="running")   # record workspace/venv

    before = _snapshot(root)
    log = logging.getLogger("saage")
    log.info("starting run %s", run_id)
    # The engine stamps the terminal completed/failed status into the final
    # checkpoint write; here we only need to record a crash (a raised exception).
    try:
        flow.run(seed)
    except BaseException:
        run.mark("failed")
        raise
    log.info("run complete")
    after = _snapshot(root)

    _print_summary(seed, before, after, root, run.dir)
    if args.verbose:                              # full agent/command outputs
        print("\nresults:")
        print(json.dumps(seed.get("results", {}), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
