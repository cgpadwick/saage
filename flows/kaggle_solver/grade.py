#!/usr/bin/env python3
"""Grade submission.csv with mlebench (optional final step; used by the sweep).

Port of mle-beast's grader.py for a single competition: writes the one-line
submissions JSONL that `mlebench grade` expects, runs it, and extracts the
medal + test score from the grading report. Tolerant of mlebench's output
format drifting — falls back to printing the raw output with MEDAL=unknown.

Prints `MEDAL=gold|silver|bronze|none|unknown TEST_SCORE=<float|nan>`.
Runs with cwd = the workspace.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def extract(report: dict) -> tuple[str, float]:
    medal = "none"
    for m in ("gold", "silver", "bronze"):
        if report.get(f"{m}_medal"):
            medal = m
            break
    score = report.get("score")
    return medal, (float(score) if score is not None else float("nan"))


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
        print("MEDAL=unknown TEST_SCORE=nan")
        sys.exit(1)

    jsonl = Path("grading_submission.jsonl")
    jsonl.write_text(json.dumps(
        {"competition_id": args.comp, "submission_path": str(sub)}) + "\n")

    cmd = ["mlebench", "grade", "--submission", str(jsonl)]
    if args.data_dir:
        cmd += ["--data-dir", args.data_dir]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        print("mlebench not installed: pip install "
              '"mlebench @ git+https://github.com/openai/mle-bench.git"',
              file=sys.stderr)
        print("MEDAL=unknown TEST_SCORE=nan")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("mlebench grade timed out", file=sys.stderr)
        print("MEDAL=unknown TEST_SCORE=nan")
        sys.exit(1)

    out = proc.stdout + "\n" + proc.stderr
    print(out)

    # mlebench writes a grading report JSON; find it next to the jsonl or in
    # the output text
    medal, score = "unknown", float("nan")
    reports = sorted(Path(".").glob("*grading_report*.json"),
                     key=lambda p: p.stat().st_mtime)
    if reports:
        try:
            data = json.loads(reports[-1].read_text())
            entries = data if isinstance(data, list) else \
                data.get("competition_reports", [data])
            for entry in entries:
                if entry.get("competition_id") in ("", None, args.comp):
                    medal, score = extract(entry)
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

    print(f"MEDAL={medal} TEST_SCORE={score}")


if __name__ == "__main__":
    main()
