"""One batched hill-climb round: K proposals → K parallel experiment runs
→ collected scores → winner. Phase-2 of docs/batched_hillclimb_plan.md.

This is the bridge between a *flow* (which stays sequential YAML — the
engine never learns what "parallel" means) and the phase-1 dispatch layer.
A coordinator flow calls it as an ordinary command step:

    python3 -m saage.remote.batch \
        --experiment-flow flows/x/experiment.yaml \
        --proposals proposals/current/p1.md proposals/current/p2.md ... \
        --targets boxa,boxb --workspace /path/to/ws \
        --results-dir batch/round_3 --set short_epochs=8

and captures the verdict from stdout (`BEST_INDEX= BEST_SCORE= BEST_PATCH=
ROUND_OK= ...`). Each proposal becomes one job: a temp copy of the
experiment flow dir is staged with the proposal at ``proposal.md`` and the
``workspace:`` key pointed at the coordinator's workspace (shipped in
bundle/ship-head mode — workers clone HEAD, never see local edits).

The experiment contract (what each job's flow must produce in its
workspace, collected via ``artifacts:``):

    eval_results.json    {"metric_name": str, "value": float}
    experiment.patch     git diff of the implemented change vs HEAD

A job with no readable score counts nan and can never win.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from .creds import get_target
from .dispatch import Dispatcher, Job

_JUNK = ("__pycache__", ".git", ".venv", "*.pyc")


@dataclass
class ExperimentResult:
    index: int
    proposal: str
    status: str
    score: float
    run_id: str | None
    target: str | None
    patch: str | None           # path to experiment.patch, if it came back

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["score"] = None if math.isnan(self.score) else self.score
        return d


def stage_job_flow(experiment_flow: Path, proposal: Path, workspace: Path,
                   stage_root: Path, index: int) -> Path:
    """Stage one job's flow dir: copy of the experiment flow dir with the
    proposal at proposal.md and `workspace:` pinned to the coordinator ws."""
    src = experiment_flow.parent
    dst = stage_root / f"job{index}"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*_JUNK))
    shutil.copyfile(proposal, dst / "proposal.md")
    flow_file = dst / experiment_flow.name
    doc = yaml.safe_load(flow_file.read_text()) or {}
    doc["workspace"] = str(workspace)
    flow_file.write_text(yaml.safe_dump(doc, sort_keys=False))
    return flow_file


def _read_score(job_dir: Path) -> float:
    try:
        data = json.loads((job_dir / "eval_results.json").read_text())
        return float(data["value"])
    except Exception:
        return float("nan")


def run_round(experiment_flow: Path, proposals: list[Path], targets: list,
              *, workspace: Path, results_dir: Path,
              set_args: dict | None = None, lower_is_better: bool = False,
              max_hours: float = 2.0, poll_interval: float = 30.0,
              stale_after: float = 120.0, provision_cmd: str | None = None,
              provision_files: Path | None = None,
              handoff_opts: dict | None = None, ops=None, clock=None,
              dispatcher_cls=Dispatcher) -> dict:
    """Dispatch one experiment per proposal, wait for all, score, pick."""
    results_dir.mkdir(parents=True, exist_ok=True)
    opts = {"dirty": "ship-head", "sync_interval": 30, **(handoff_opts or {})}

    stage_root = Path(tempfile.mkdtemp(prefix="saage_batch_"))
    jobs = []
    for i, prop in enumerate(proposals):
        flow_file = stage_job_flow(experiment_flow, prop, workspace,
                                   stage_root, i)
        jobs.append(Job(name=f"p{i}", flow_file=str(flow_file),
                        set_args={**(set_args or {}), "job_index": str(i)}))

    extra = {"clock": clock} if clock is not None else {}
    d = dispatcher_cls(str(experiment_flow), jobs, targets, ops=ops,
                       provision_cmd=provision_cmd,
                       provision_files=provision_files,
                       max_hours=max_hours, poll_interval=poll_interval,
                       stale_after=stale_after, fetch_dest=results_dir,
                       dispatch_workers=max(4, len(jobs)),
                       handoff_opts=opts, **extra)
    d.run()
    shutil.rmtree(stage_root, ignore_errors=True)

    results = []
    for i, (job, prop) in enumerate(zip(jobs, proposals)):
        job_dir = results_dir / job.name
        score = _read_score(job_dir) if job.status == "done" else float("nan")
        patch = job_dir / "experiment.patch"
        results.append(ExperimentResult(
            index=i, proposal=str(prop), status=job.status, score=score,
            run_id=job.run_id, target=job.target,
            patch=str(patch) if patch.is_file() else None))

    def better(a: float, b: float) -> bool:
        if math.isnan(a):
            return False
        if math.isnan(b):
            return True
        return a < b if lower_is_better else a > b

    best = None
    for r in results:
        usable = r.patch and not math.isnan(r.score)
        if usable and (best is None or better(r.score, best.score)):
            best = r

    summary = {
        "proposals": [r.to_dict() for r in results],
        "ok": sum(1 for r in results if r.status == "done"),
        "failed": sum(1 for r in results if r.status != "done"),
        "best_index": best.index if best else None,
        "best_score": (best.score if best else float("nan")),
        "best_patch": (best.patch if best else None),
        "lower_is_better": lower_is_better,
    }
    (results_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--experiment-flow", required=True)
    ap.add_argument("--proposals", nargs="+", required=True)
    ap.add_argument("--targets", required=True, help="comma-separated names")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--set", dest="set_args", action="append", default=[],
                    metavar="KEY=VALUE")
    ap.add_argument("--lower-is-better", default="false", type=str.lower,
                    choices=["true", "false"])    # templates render bools as True/False
    ap.add_argument("--max-hours", type=float, default=2.0)
    ap.add_argument("--poll-interval", type=float, default=30.0)
    ap.add_argument("--provision-cmd", default=None)
    ap.add_argument("--provision-files", default=None, metavar="DIR",
                    help="local dir rsynced into the provision cwd on each "
                         "node (e.g. prepared competition data)")
    ap.add_argument("--bootstrap-timeout", type=int, default=1800)
    ap.add_argument("--need-gpu", action="store_true")
    args = ap.parse_args(argv)

    set_args = dict(kv.split("=", 1) for kv in args.set_args)
    targets = [get_target(n) for n in args.targets.split(",") if n]
    summary = run_round(
        Path(args.experiment_flow).resolve(),
        [Path(p) for p in args.proposals],
        targets,
        workspace=Path(args.workspace).resolve(),
        results_dir=Path(args.results_dir).resolve(),
        set_args=set_args,
        lower_is_better=args.lower_is_better == "true",
        max_hours=args.max_hours, poll_interval=args.poll_interval,
        provision_cmd=args.provision_cmd,
        provision_files=Path(args.provision_files) if args.provision_files else None,
        handoff_opts={"bootstrap_timeout": args.bootstrap_timeout,
                      "need_gpu": args.need_gpu},
    )
    for r in summary["proposals"]:
        print(f"  p{r['index']}: status={r['status']} score={r['score']} "
              f"target={r['target']}")
    print(f"ROUND_OK={summary['ok']}")
    print(f"ROUND_FAILED={summary['failed']}")
    print(f"BEST_INDEX={summary['best_index'] if summary['best_index'] is not None else -1}")
    print(f"BEST_SCORE={summary['best_score']}")
    print(f"BEST_PATCH={summary['best_patch'] or ''}")
    return 0 if summary["ok"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
