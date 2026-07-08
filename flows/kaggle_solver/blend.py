#!/usr/bin/env python3
"""Blend submissions from independent kaggle_solver runs (offline P3 evidence).

Equal-weight blend of per-class probability submissions — geometric mean by
default (the right combiner for logloss metrics; use --arith for arithmetic).
Rows are matched by the first column (id); all files must share the exact
header. The recipe is deliberately parameter-free (equal weights, one declared
combiner) so a blend can be graded WITHOUT test-set tuning.

Measured on spooky-author-identification (2026-07-04): runs graded 0.34808
and 0.36381 alone; their geometric blend graded 0.29470 — a 15% gain, 0.0009
short of bronze. Diverse errors cancel. The in-run version of this lives in
the ensemble skill (cross-family blending from git history).

Usage:
    python blend.py a.csv b.csv [c.csv ...] -o blended.csv [--arith]
"""
from __future__ import annotations

import argparse
import csv
import math
import sys


def read_sub(path: str) -> tuple[list[str], list[str], dict[str, list[float]]]:
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    probs = {r[0]: [float(x) for x in r[1:]] for r in body}
    return header, [r[0] for r in body], probs


def main() -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("subs", nargs="+", help="two or more submission CSVs")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--arith", action="store_true",
                    help="arithmetic mean (default: geometric)")
    args = ap.parse_args()
    if len(args.subs) < 2:
        sys.exit("need at least two submissions to blend")

    header, ids, first = read_sub(args.subs[0])
    all_probs = [first]
    for p in args.subs[1:]:
        h, i, probs = read_sub(p)
        if h != header or set(i) != set(ids):
            sys.exit(f"{p}: header/ids don't match {args.subs[0]}")
        all_probs.append(probs)

    n = len(all_probs)
    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for rid in ids:
            cols = zip(*(p[rid] for p in all_probs))
            if args.arith:
                m = [sum(c) / n for c in cols]
            else:
                m = [math.exp(sum(math.log(max(x, 1e-15)) for x in c) / n)
                     for c in cols]
            s = sum(m)
            w.writerow([rid] + [f"{x / s:.10f}" for x in m])
    print(f"blended {n} submissions ({'arith' if args.arith else 'geometric'}) "
          f"-> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
