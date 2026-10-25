"""`saage host …` — drive a host-mode run (saage.host) from any shell.

The CLI twin of the MCP tools start_hosted_run / next_task / submit_result,
for agents without MCP (or with it unregistered), scripts, and humans:

    saage host start flow.yaml [--set k=v]   → prints the run id, returns at once
    saage host next  [RUN] [--timeout S]     → blocks until the next agent step
    saage host submit [RUN] SEQ < answer     → answers it
    saage host status [RUN]

Runs are found in the run store (~/.saage/runs), so any host-mode run is
drivable here — started by `saage host start`, `saage run --provider host`,
MCP, the web UI, or resumed. RUN is an id or unique prefix; omitted, it is
the one live hosted run (several live → name one, so an answer can never
land in the wrong run by omission).

`next` exit codes are the protocol, so a shell loop can drive a run:
    0 a step was printed · 3 the run is finished · 4 still busy (call again) · 1 error
(2 is argparse's usage error, so it is not used.)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXIT_STEP, EXIT_ERROR, EXIT_DONE, EXIT_BUSY = 0, 1, 3, 4


def add_parser(sub: argparse._SubParsersAction) -> None:
    h = sub.add_parser("host", help="drive a host-mode run (you/your agent are "
                                    "the LLM): start | next | submit | status")
    hs = h.add_subparsers(dest="host_cmd", required=True)
    st = hs.add_parser("start", help="start FLOW in host mode in the background; "
                                     "prints the run id")
    st.add_argument("flow", metavar="flow.yaml")
    st.add_argument("--set", dest="overrides", metavar="KEY=VALUE", action="append",
                    default=[], help="override a shared-store knob (repeatable)")
    st.add_argument("--workspace", metavar="DIR")
    nx = hs.add_parser("next", help="block until the run's next agent step and "
                                    "print it (exit 0 step, 3 done, 4 busy)")
    nx.add_argument("run_id", nargs="?")
    nx.add_argument("--timeout", type=float, default=90.0, metavar="SECONDS",
                    help="give up waiting after this long with exit 4 (default "
                         "90 — under an agent's usual command timeout)")
    nx.add_argument("--json", action="store_true", help="print the step as JSON")
    sb = hs.add_parser("submit", help="answer step SEQ with the text on stdin "
                                      "(or --file)")
    sb.add_argument("args", nargs="+", metavar="[RUN] SEQ")
    sb.add_argument("--file", metavar="PATH", help="read the answer from PATH")
    ss = hs.add_parser("status", help="status of a hosted run")
    ss.add_argument("run_id", nargs="?")


def _err(msg: str) -> int:
    print(f"saage host: error: {msg}", file=sys.stderr)
    return EXIT_ERROR


def _print_step(run_id: str, p: dict) -> None:
    print(f"run {run_id} · step {p['seq']}: {p['step_id']} (skill: {p['skill']})")
    print(f"tools: {', '.join(p['tools']) or '(none)'}  — use your own equivalents")
    if p.get("workspace"):
        print(f"workspace: {p['workspace']}  — relative paths in the task mean files here")
    print("\n--- instructions (system) ---\n" + p["system"].rstrip())
    print("\n--- task ---\n" + p["task"].rstrip())
    print("\n--- answer with ONLY the final output the task asks for: ---")
    print(f"saage host submit {run_id} {p['seq']} <<'EOF'\n<answer>\nEOF")


def _cmd_start(args) -> int:
    from .server.catalog import load_flow_info
    from .server.jobs import JobRegistry
    fy = Path(args.flow).resolve()
    if not fy.is_file():
        return _err(f"flow file not found: {args.flow}")
    info = load_flow_info(fy.parent.name, fy)
    if info.error:
        return _err(f"flow is broken: {info.error}")
    overrides = {}
    for kv in args.overrides:
        k, sep, v = kv.partition("=")
        if not sep:
            return _err(f"--set expects KEY=VALUE, got {kv!r}")
        overrides[k] = v
    # the child runs with cwd = the flow's dir: resolve against OUR cwd, the
    # way `saage run --workspace` does
    ws = str(Path(args.workspace).resolve()) if args.workspace else None
    try:
        job = JobRegistry().launch(info, overrides, workspace=ws, provider="host")
    except ValueError as e:                          # unknown knob
        return _err(str(e))
    # make the run findable NOW: the child creates its mailbox only once it
    # has hydrated the flow, and an immediate `saage host next` must not miss it
    from .host import mailbox_dir
    from .paths import runs_dir
    mailbox_dir(runs_dir() / job.job_id).mkdir(parents=True, exist_ok=True)
    print(job.job_id)
    print(f"started; next: saage host next {job.job_id}", file=sys.stderr)
    return 0


def _cmd_next(run_dir: Path, args) -> int:
    from .host import wait_next
    kind, val = wait_next(run_dir, max(0.0, args.timeout))
    if kind == "task":
        if args.json:
            print(json.dumps(val))
        else:
            _print_step(run_dir.name, val)
        return EXIT_STEP
    if kind == "done":
        print(json.dumps({"done": True, "status": val}) if args.json
              else f"run {run_dir.name} finished: {val}")
        return EXIT_DONE
    print(json.dumps({"waiting": True, "status": val}) if args.json
          else f"run {run_dir.name} is busy (a command step?) — run "
               f"`saage host next {run_dir.name}` again", file=sys.stderr)
    return EXIT_BUSY


def _cmd_submit(runs: Path, args) -> int:
    from .host import resolve_run, submit
    if len(args.args) > 2:
        return _err("usage: saage host submit [RUN] SEQ")
    ref, seq = (None, args.args[0]) if len(args.args) == 1 else args.args
    try:
        seq_n = int(seq)
    except ValueError:
        return _err(f"SEQ must be an integer, got {seq!r}")
    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            return _err(f"can't read --file: {e}")
    else:
        text = sys.stdin.read()
    try:
        run_dir = resolve_run(runs, ref)
        submit(run_dir, seq_n, text.rstrip("\n"))
    except ValueError as e:              # ambiguous run / stale / duplicate / dead
        return _err(str(e))
    print(f"ok; next: saage host next {run_dir.name}", file=sys.stderr)
    return 0


def _cmd_status(run_dir: Path) -> int:
    from .host import mailbox_dir, read_pending, run_status
    st = run_status(run_dir)
    print(f"run {run_dir.name}: {st}")
    p = read_pending(mailbox_dir(run_dir)) if st == "running" else None
    if p is not None:
        print(f"waiting on step {p['seq']}: {p['step_id']} (skill: {p['skill']})")
    return 0


def dispatch(args) -> int:
    from .host import resolve_run
    from .paths import runs_dir
    if args.host_cmd == "start":
        return _cmd_start(args)
    if args.host_cmd == "submit":
        return _cmd_submit(runs_dir(), args)
    try:
        run_dir = resolve_run(runs_dir(), args.run_id)
    except ValueError as e:
        return _err(str(e))
    if args.host_cmd == "next":
        return _cmd_next(run_dir, args)
    return _cmd_status(run_dir)
