#!/usr/bin/env python3
"""Benchmark sweep driver for the kaggle_solver flow (M3 in the plan).

One command fans a set of mle-bench competitions out to cloud GPU boxes
(one `saage remote handoff` per competition), polls the R2 mirror until the
runs finish, collects each run's grade + cost, and maintains the results
journal that backs BENCHMARK_RESULTS.md — the "medals per dollar" table.

The driver deliberately owns NO remote logic: every action shells out to the
`saage remote` CLI (spawn/handoff/terminate), and status/results are read
from the R2 mirror with the same credentials saage itself uses. It is a thin,
restartable orchestrator: state lives in the run ledgers and the journal
file, so a killed sweep can be re-collected any time with `bench.py collect`.

Usage:
    # launch: one spawned a10 box per comp (or --targets t1,t2 to reuse boxes)
    python flows/kaggle_solver/bench.py sweep \
        --comps spooky-author-identification,nomad2018-predict-transparent-conductors \
        --spawn a10 [--wait] [--terminate]

    # gather grades/costs from finished runs into the journal + table
    python flows/kaggle_solver/bench.py collect --runs <id>[,<id>…]
    python flows/kaggle_solver/bench.py table      # re-render the md table only
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

FLOW_DIR = Path(__file__).resolve().parent
FLOW_YAML = FLOW_DIR / "flow.yaml"
JOURNAL = FLOW_DIR / "benchmark_journal.jsonl"
RESULTS_MD = FLOW_DIR / "BENCHMARK_RESULTS.md"

# metric direction per competition (mle-bench Lite subset we run) — the one
# per-comp fact the flow can't infer before reading the description
LOWER_IS_BETTER = {
    "spooky-author-identification": True,             # multiclass logloss
    "nomad2018-predict-transparent-conductors": True, # RMSLE
    "tabular-playground-series-may-2022": False,      # AUC
    "random-acts-of-pizza": False,                    # AUC
    "detecting-insults-in-social-commentary": False,  # AUC
    "aerial-cactus-identification": False,            # AUC
    "leaf-classification": True,                      # logloss
    "text-normalization-challenge-english-language": False,  # accuracy
}


@dataclass
class RunResult:
    """One journal record — everything the results table needs."""
    run_id: str
    competition: str
    model: str = "?"
    medal: str = "unknown"
    val_score: str = "?"
    test_score: str = "?"
    above_median: str = "?"
    llm_cost_usd: str = "?"
    tokens: str = "?"
    gpu_hours: str = "?"
    date: str = "?"


# --------------------------------------------------------------------------- #
# pure helpers (unit-tested offline)
# --------------------------------------------------------------------------- #
def parse_run_summary(log_text: str) -> dict:
    """Pull tokens/cost out of a mirrored saage.log's run summary block."""
    out = {}
    m = re.search(r"tokens:\s*([\d,]+)\s*\(", log_text)
    if m:
        out["tokens"] = m.group(1)
    m = re.search(r"cost:\s*~\$([0-9.]+)", log_text)
    if m:
        out["llm_cost_usd"] = m.group(1)
    return out


def result_from_checkpoint(run_id: str, shared: dict) -> RunResult:
    """Grade fields the flow captured into the shared store (grade.py output)."""
    def s(key, default="?"):
        v = shared.get(key, default)
        return default if v in ("", None) else str(v)
    return RunResult(
        run_id=run_id,
        competition=s("competition_id"),
        medal=s("medal", "unknown"),
        val_score=s("best_score"),
        test_score=s("test_score"),
        above_median=s("above_median"),
    )


def upsert_journal(journal_path: Path, rec: RunResult) -> list[dict]:
    """Append-or-replace by run_id; returns the full journal, newest last."""
    rows = []
    if journal_path.exists():
        rows = [json.loads(l) for l in journal_path.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("run_id") != rec.run_id]
    rows.append(asdict(rec))
    journal_path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return rows


def render_table(rows: list[dict]) -> str:
    """BENCHMARK_RESULTS.md — the brag page, regenerated from the journal."""
    head = (
        "# kaggle_solver benchmark results\n\n"
        "Autonomous runs of `flows/kaggle_solver` graded with `mlebench grade`.\n"
        "Regenerate with `python flows/kaggle_solver/bench.py table` — the\n"
        "source of truth is `benchmark_journal.jsonl` (one line per run).\n\n"
        "| date | competition | model | medal | above median | val score | "
        "test score | LLM cost | GPU hours | run |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n")
    body = "".join(
        f"| {r.get('date','?')} | {r.get('competition','?')} | {r.get('model','?')} "
        f"| {r.get('medal','?')} | {r.get('above_median','?')} | {r.get('val_score','?')} "
        f"| {r.get('test_score','?')} | ${r.get('llm_cost_usd','?')} "
        f"| {r.get('gpu_hours','?')} | `{r.get('run_id','?')}` |\n"
        for r in rows)
    return head + body


def memory_note(rec: RunResult, research_log: str) -> str:
    """Cross-competition memory (P5): the distilled note a future run's agents
    read via the flow's stage_memory step. Header = the graded outcome; body =
    the run's own research log (already kept terse for per-iteration re-reads),
    truncated defensively."""
    body = research_log.strip()
    if len(body) > 8000:
        body = body[:8000] + "\n… (truncated)"
    return (f"# {rec.competition} — run {rec.run_id}\n\n"
            f"outcome: medal={rec.medal} above_median={rec.above_median} "
            f"val={rec.val_score} test={rec.test_score} "
            f"llm_cost=${rec.llm_cost_usd}\n\n"
            f"## Research log\n\n{body}\n")


def gpu_hours(started_iso: str, updated_iso: str) -> str:
    from datetime import datetime
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        dt = (datetime.strptime(updated_iso, fmt)
              - datetime.strptime(started_iso, fmt)).total_seconds() / 3600
        return f"{dt:.1f}"
    except (ValueError, TypeError):
        return "?"


# --------------------------------------------------------------------------- #
# mirror access (same creds saage uses)
# --------------------------------------------------------------------------- #
def _storage():
    from saage.remote.creds import storage_config
    st = storage_config()
    if st is None:
        sys.exit("bench needs a [storage] section in ~/.saage/credentials.toml "
                 "(the R2 mirror is how it observes runs)")
    return st


def _client(st):
    import boto3
    return boto3.client("s3", endpoint_url=st.endpoint,
                        aws_access_key_id=st.access_key,
                        aws_secret_access_key=st.secret_key)


def _mirror_json(st, run_id: str, name: str) -> dict:
    try:
        obj = _client(st).get_object(Bucket=st.bucket,
                                     Key=f"{st.run_prefix(run_id)}/{name}")
        return json.loads(obj["Body"].read())
    except Exception:
        return {}


def _mirror_text(st, run_id: str, name: str) -> str:
    try:
        obj = _client(st).get_object(Bucket=st.bucket,
                                     Key=f"{st.run_prefix(run_id)}/{name}")
        return obj["Body"].read().decode("utf-8", "replace")
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# actions (shell out to the saage CLI — no remote logic duplicated here)
# --------------------------------------------------------------------------- #
def _saage(*args: str) -> str:
    cmd = [sys.executable, "-m", "saage.cli", "remote", *args]
    print("+", " ".join(args))
    p = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    if p.returncode != 0:
        raise SystemExit(f"saage remote {args[0]} failed (rc={p.returncode})")
    return p.stdout + p.stderr


def _kaggle_creds() -> tuple[str, str]:
    f = Path.home() / ".kaggle" / "kaggle.json"
    if not f.exists():
        sys.exit("~/.kaggle/kaggle.json not found (cloud_setup needs it to "
                 "prepare competition data on the node)")
    d = json.loads(f.read_text())
    return d["username"], d["key"]


def sweep(args) -> int:
    comps = [c.strip() for c in args.comps.split(",") if c.strip()]
    targets = [t.strip() for t in args.targets.split(",")] if args.targets else []
    if targets and len(targets) != len(comps):
        sys.exit(f"{len(comps)} comps but {len(targets)} targets")
    ku, kk = _kaggle_creds()

    launched: list[tuple[str, str, str]] = []   # (comp, target, run_id)
    for i, comp in enumerate(comps):
        lib = LOWER_IS_BETTER.get(comp)
        if lib is None and not args.assume_higher:
            sys.exit(f"unknown metric direction for {comp!r} — add it to "
                     f"LOWER_IS_BETTER in bench.py (or pass --assume-higher)")
        if targets:
            target = targets[i]
        else:
            target = f"bench-{comp.split('-')[0][:12]}"
            _saage("spawn", "--gpu", args.spawn, "--name", target)
        out = _saage(
            "handoff", str(FLOW_YAML), "--target", target,
            "--set", f"competition_id={comp}",
            "--set", f"lower_is_better={'true' if lib else 'false'}",
            "--env", f"COMP={comp}",
            "--env", f"KAGGLE_USERNAME={ku}", "--env", f"KAGGLE_KEY={kk}",
            "--env", "SAAGE_SEARCH_BLOCK_DOMAINS=kaggle.com",
            "--ws-setup", "bash ../flow/cloud_setup.sh",
            "--bootstrap-timeout", "3000")
        m = re.search(r"run (\S+) handed off", out)
        run_id = m.group(1) if m else "?"
        launched.append((comp, target, run_id))
        print(f"→ {comp} on {target}: {run_id}")

    if not args.wait:
        print("\nlaunched — poll with `bench.py collect --runs "
              + ",".join(r for _, _, r in launched) + "` when done")
        return 0

    st = _storage()
    pending = {r: (c, t) for c, t, r in launched}
    while pending:
        time.sleep(args.poll_seconds)
        for run_id in list(pending):
            phase = _mirror_json(st, run_id, "status.json").get("phase", "?")
            print(f"  {run_id}: {phase}")
            if phase in ("done", "failed", "killed"):
                comp, target = pending.pop(run_id)
                collect_one(st, run_id)
                if args.terminate and not args.targets:
                    _saage("terminate", target)
    print(render_table_from_journal())
    return 0


def collect_one(st, run_id: str) -> RunResult:
    ck = _mirror_json(st, run_id, "checkpoint.json")
    rec = result_from_checkpoint(run_id, ck.get("shared", {}))
    log_text = _mirror_text(st, run_id, "saage.log")
    for k, v in parse_run_summary(log_text).items():
        setattr(rec, k, v)
    # cross-competition memory (P5): future runs read this via stage_memory
    research_log = _mirror_text(st, run_id, "artifacts/research_log.md")
    if research_log and rec.competition != "?":
        mem_dir = FLOW_DIR / "memory"
        mem_dir.mkdir(exist_ok=True)
        (mem_dir / f"{rec.competition}.md").write_text(
            memory_note(rec, research_log))
    # model + timing from the local ledger, when this machine did the handoff
    from saage.remote.state import RunState
    rs = RunState(run_id)
    if rs.exists():
        state = rs.state()
        manifest = rs.manifest() or {}
        rec.model = manifest.get("provider", "?")
        started = state.get("started", "?")
        rec.date = started[:10] if started != "?" else "?"
        updated = _mirror_json(st, run_id, "status.json").get("updated", "?")
        rec.gpu_hours = gpu_hours(started, updated)
    upsert_journal(JOURNAL, rec)
    print(f"✓ {run_id}: {rec.competition} medal={rec.medal} "
          f"test={rec.test_score} cost=${rec.llm_cost_usd}")
    return rec


def render_table_from_journal() -> str:
    rows = []
    if JOURNAL.exists():
        rows = [json.loads(l) for l in JOURNAL.read_text().splitlines() if l.strip()]
    md = render_table(rows)
    RESULTS_MD.write_text(md)
    return md


def main() -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sw = sub.add_parser("sweep", help="handoff one run per competition")
    sw.add_argument("--comps", required=True, help="comma-separated comp ids")
    sw.add_argument("--targets", help="reuse existing targets (comma-separated, "
                                      "one per comp); default: spawn new boxes")
    sw.add_argument("--spawn", default="a10", help="GPU class when spawning")
    sw.add_argument("--wait", action="store_true",
                    help="poll until all runs finish, collecting as they land")
    sw.add_argument("--terminate", action="store_true",
                    help="terminate each spawned box when its run finishes")
    sw.add_argument("--poll-seconds", type=int, default=300)
    sw.add_argument("--assume-higher", action="store_true",
                    help="treat comps missing from LOWER_IS_BETTER as "
                         "higher-is-better instead of refusing")

    co = sub.add_parser("collect", help="pull grades/costs into the journal")
    co.add_argument("--runs", required=True, help="comma-separated run ids")

    sub.add_parser("table", help="re-render BENCHMARK_RESULTS.md from the journal")

    args = ap.parse_args()
    if args.cmd == "sweep":
        return sweep(args)
    if args.cmd == "collect":
        st = _storage()
        for run_id in [r.strip() for r in args.runs.split(",") if r.strip()]:
            collect_one(st, run_id)
        print(render_table_from_journal())
        return 0
    if args.cmd == "table":
        print(render_table_from_journal())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
