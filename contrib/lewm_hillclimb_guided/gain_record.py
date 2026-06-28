#!/usr/bin/env python3
"""Record the task-specialization gain (deterministic — no LLM).

gain = specialized_test_score - paper_recipe_test_score, both measured on the
same held-out test split. Appends a human-readable block to research_log.md and
prints `GAIN=<value>` for the flow's `set:` capture. Never aborts the run: a
crashed headline reads as the -1 sentinel and is reported as-is.
"""
from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--specialized", type=float, required=True)
    ap.add_argument("--paper", type=float, required=True)
    ap.add_argument("--dino", type=float, default=86.0)
    args = ap.parse_args()

    gain = round(args.specialized - args.paper, 1)
    with open("research_log.md", "a") as f:
        f.write(
            "\n## Specialization result (held-out test, single seed)\n"
            f"- paper-recipe test success_rate: {args.paper:g}\n"
            f"- specialized test success_rate: {args.specialized:g}\n"
            f"- specialization gain: {gain:+.1f} points\n"
            f"- reference: DINO-WM = {args.dino:g}\n"
        )
    print(f"GAIN={gain}")


if __name__ == "__main__":
    main()
