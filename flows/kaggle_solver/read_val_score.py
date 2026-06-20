#!/usr/bin/env python3
"""Print the validation score from eval_results.json (deterministic — no LLM).

The train.py contract: at exit it writes eval_results.json at the workspace
root as {"metric_name": <str>, "value": <float>}. This helper turns that into
a `VAL_SCORE=<float>` line for a `set:` capture — the score that drives
keep/revert comes from this file, never from log prose (and unlike
greenfield's read_score.py, no [0,1] range check: kaggle metrics are
arbitrary — RMSE, logloss, AUC...).

Prints VAL_SCORE=nan (which keep_or_revert treats as "revert") when the file
is missing or malformed, so a crashed training run can never score.
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    try:
        data = json.load(open("eval_results.json"))
        value = float(data["value"])
    except Exception as exc:                       # missing/malformed -> sentinel
        print(f"read_val_score: {exc}", file=sys.stderr)
        print("VAL_SCORE=nan")
        return
    print(f"METRIC={data.get('metric_name', 'unknown')}")
    print(f"VAL_SCORE={value}")


if __name__ == "__main__":
    main()
