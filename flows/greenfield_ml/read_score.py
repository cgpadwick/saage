#!/usr/bin/env python3
"""Read the held-out score from eval_results.json (the source of truth) and print
`SCORE=<value>` for the flow to capture — deterministic, no LLM.

This replaces capturing the score from the agent/script's free-text `Test accuracy:`
line, which was gameable and scale-blind (a stray `Test accuracy: 98` would parse as
98.0 and instantly "meet" a 0.97 target). Here the value comes from the structured
eval_results.json and is validated to be a finite fraction in [0, 1]; on ANY problem
(missing file, bad JSON, out-of-range value) it prints a diagnostic line WITHOUT a
`SCORE=` token, so the flow's `set:` capture finds no match and leaves the previous
score untouched rather than trusting junk.

Runs in the workspace (the command's cwd).
"""
from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    p = Path("eval_results.json")
    if not p.exists():
        print("SCORE_MISSING: eval_results.json not found")
        return
    try:
        value = float(json.loads(p.read_text())["value"])
    except Exception as e:                       # malformed JSON / missing key / non-numeric
        print(f"SCORE_ERROR: {e}")
        return
    if not (value == value and 0.0 <= value <= 1.0):   # finite and in [0, 1]
        print(f"SCORE_OUT_OF_RANGE: {value} (expected a fraction in [0,1])")
        return
    print(f"SCORE={value}")


if __name__ == "__main__":
    main()
