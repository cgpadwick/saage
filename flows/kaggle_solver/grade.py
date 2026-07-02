#!/usr/bin/env python3
"""Grade submission.csv with mlebench (optional final step; used by the sweep).

Port of mle-beast's grader.py for a single competition: writes the one-line
submissions JSONL that `mlebench grade` expects, runs it, and extracts the
medal + test score from the grading report. Tolerant of mlebench's output
format drifting — falls back to printing the raw output with MEDAL=unknown.

Prints `MEDAL=gold|silver|bronze|none|unknown TEST_SCORE=<float|nan>
ABOVE_MEDIAN=true|false|unknown`.
Runs with cwd = the workspace.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def extract(report: dict) -> tuple[str, float, bool]:
    medal = "none"
    for m in ("gold", "silver", "bronze"):
        if report.get(f"{m}_medal"):
            medal = m
            break
    score = report.get("score")
    return (medal, (float(score) if score is not None else float("nan")),
            bool(report.get("above_median")))


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--comp", required=True)
    ap.add_argument("--submission", default="submission.csv")
    ap.add_argument("--data-dir", default="",
                    help="passed to mlebench grade when set")
    args = ap.parse_args()

    sub = Path(args.submission).resolve()
    if not sub.exists():
        print(f"ERROR: {sub} not found", file=sys.stderr)
        print("MEDAL=unknown TEST_SCORE=nan ABOVE_MEDIAN=unknown")
        sys.exit(1)

    jsonl = Path("grading_submission.jsonl")
    jsonl.write_text(json.dumps(
        {"competition_id": args.comp, "submission_path": str(sub)}) + "\n")

    # mlebench >=0.1 requires --output-dir (the report JSON lands there, not in
    # cwd); older builds ignored it. Omitting it silently failed grading — the
    # command errored, `|| true` in the flow masked it, and MEDAL came back
    # unknown. Always pass it and read the report from there.
    out_dir = Path("grade_out")
    out_dir.mkdir(exist_ok=True)
    cmd = ["mlebench", "grade", "--submission", str(jsonl),
           "--output-dir", str(out_dir)]
    if args.data_dir:
        cmd += ["--data-dir", args.data_dir]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        print("mlebench not installed: pip install "
              '"mlebench @ git+https://github.com/openai/mle-bench.git"',
              file=sys.stderr)
        print("MEDAL=unknown TEST_SCORE=nan ABOVE_MEDIAN=unknown")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("mlebench grade timed out", file=sys.stderr)
        print("MEDAL=unknown TEST_SCORE=nan ABOVE_MEDIAN=unknown")
        sys.exit(1)

    out = proc.stdout + "\n" + proc.stderr
    print(out)

    # the report lands in --output-dir; fall back to cwd for older mlebench.
    medal, score, above = "unknown", float("nan"), None
    reports = sorted([*out_dir.glob("*grading_report*.json"),
                      *Path(".").glob("*grading_report*.json")],
                     key=lambda p: p.stat().st_mtime)
    if reports:
        try:
            data = json.loads(reports[-1].read_text())
            entries = data if isinstance(data, list) else \
                data.get("competition_reports", [data])
            for entry in entries:
                if entry.get("competition_id") in ("", None, args.comp):
                    medal, score, above = extract(entry)
                    break
        except Exception:
            pass
    if medal == "unknown":                      # last resort: scrape stdout
        m = re.search(r'"(gold|silver|bronze)_medal":\s*true', out)
        if m:
            medal = m.group(1)
        elif re.search(r'"any_medal":\s*false', out):
            medal = "none"
        s = re.search(r'"score":\s*([0-9.eE+-]+)', out)
        if s:
            score = float(s.group(1))
        a = re.search(r'"above_median":\s*(true|false)', out)
        if a:
            above = a.group(1) == "true"

    above_str = "unknown" if above is None else str(above).lower()
    print(f"MEDAL={medal} TEST_SCORE={score} ABOVE_MEDIAN={above_str}")


if __name__ == "__main__":
    main()
