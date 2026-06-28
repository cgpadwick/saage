#!/usr/bin/env python3
"""Remove a saage experiment's checkpoint + eval-artifact dirs (deterministic).

train.py resumes from `$STABLEWM_HOME/checkpoints/<name>/<name>_weights.ckpt`
when it exists, so each experiment must start from a CLEAN directory or it
would silently continue the previous experiment's weights. eval.py writes
videos + results under `$STABLEWM_HOME/<name>/`; cleaned too so artifacts
belong to one experiment.

Only saage-owned names are deletable; the user's own checkpoints
(lewm, lewm_cube, lewm_reacher, ...) are protected.

Usage: python3 clean_ckpt.py --name lewm_cube_exp [--name lewm_smoke ...]
(Also staged into the workspace as `saage_clean_ckpt.py` for the verify agent.)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# explicit allow-list: the only directories this script may ever delete
ALLOWED = {"lewm_cube_exp", "lewm_cube_best", "lewm_smoke",
           "lewm_cube_confirm",   # the winner-confirmation retrain
           "lewm_cube_paper"}     # the paper-recipe headline retrain


def cache_dir() -> Path:
    return Path(os.environ.get("STABLEWM_HOME", Path.home() / ".stable-wm"))


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--name", action="append", required=True)
    args = ap.parse_args()

    for name in args.name:
        if name not in ALLOWED:
            print(f"ERROR: refusing to clean {name!r} (allowed: {sorted(ALLOWED)})",
                  file=sys.stderr)
            sys.exit(1)
        for d in (cache_dir() / "checkpoints" / name, cache_dir() / name):
            if d.exists():
                shutil.rmtree(d)
        print(f"CLEANED={name}")


if __name__ == "__main__":
    main()
