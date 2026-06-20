#!/usr/bin/env python3
"""Validate submission.csv against the competition contract (deterministic).

Port of mle-beast's SubmissionCriticNode: columns and row count must match
sample_submission.csv exactly, no empty/NaN cells. Prints the discrepancies
as feedback (re-injected into the make_submission agent on retry) and ends
with `ACTION: pass|fail` so it serves directly as a retry_loop check (E2).
On pass, pins the submission + final artifacts into git.

Runs with cwd = the workspace.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


def fail(*issues: str) -> None:
    for issue in issues:
        print(f"ISSUE: {issue}")
    print("ACTION: fail")
    sys.exit(0)        # the verdict is the ACTION, not the exit code


def main() -> None:
    sample = Path("sample_submission.csv")
    if not sample.exists():
        fail("sample_submission.csv missing from workspace — prepare_comp.py not run?")
    with open(sample, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        expected_cols = next(reader, None) or []
        expected_rows = sum(1 for _ in reader)

    sub = Path("submission.csv")
    if not sub.exists():
        fail("submission.csv does not exist. Run predict.py to produce it.")

    try:
        with open(sub, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            rows = 0
            bad_cells = 0
            for row in reader:
                rows += 1
                for cell in row[1:]:           # value columns (first is the id)
                    if cell.strip() == "" or cell.strip().lower() == "nan":
                        bad_cells += 1
    except Exception as exc:
        fail(f"failed to read submission.csv: {exc}")

    issues = []
    if header is None:
        issues.append("submission.csv is empty (no header row)")
    elif header != expected_cols:
        issues.append(f"column mismatch: got {header}, expected {expected_cols}")
    if expected_rows and rows != expected_rows:
        issues.append(f"row count mismatch: got {rows}, expected {expected_rows}")
    if bad_cells:
        issues.append(f"{bad_cells} empty/NaN value cell(s)")
    if issues:
        fail(*issues)

    # pin the final state so the workspace ends clean and reproducible
    git = ["git", "-c", "user.email=saage@local", "-c", "user.name=saage"]
    subprocess.run([*git, "add", "-A", "-f", "submission.csv"], capture_output=True)
    subprocess.run([*git, "add", "-A"], capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", "saage: final submission"],
                   capture_output=True)

    print(f"submission ok: {rows} rows, columns match")
    print("ACTION: pass")


if __name__ == "__main__":
    main()
