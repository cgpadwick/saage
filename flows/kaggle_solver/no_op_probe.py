#!/usr/bin/env python3
"""Deterministic no-op detector for the implement gate.

The trap this closes (seen repeatedly, live): the harness runs
`python3 train.py` with no flags, and implementers deliver changes as
flag-gated variants or unwired classes — the worktree changes, pytest
passes, and the champion pipeline re-runs bit-identically, wasting a whole
iteration to learn nothing. Prompt warnings reduce but do not stop it.

Probe: run a 1-epoch train on CPU into a throwaway checkpoint dir, then
compare the bytes of predictions/val_preds.csv against the newest archived
pool member's. Identical bytes ⇒ the default execution path is unchanged ⇒
FAIL (exit 1) with the trap message — the retry loop feeds it back to the
implementer before any real budget is spent.

FAILS OPEN by design (exit 0) whenever it cannot conclude: no pool
reference yet (baseline), the probe train crashes or times out (the real
train + verifier own that diagnosis), or predictions are missing. A
stochastic model whose 1-epoch preds differ run-to-run also passes — this
probe only catches the deterministic-no-op signature, which is exactly the
observed failure mode. cwd = workspace.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

PROBE_TIMEOUT = 300
PROBE_CKPT = Path(".probe_ckpt")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    pool = sorted(Path("ensemble_pool").glob("*/val_preds.csv")) if Path(
        "ensemble_pool").is_dir() else []
    if not pool:
        print("NOOP_PROBE=pass reason=no-reference (baseline)")
        return 0
    ref = pool[-1]

    try:
        r = subprocess.run(
            [sys.executable, "train.py", "--device", "cpu", "--epochs", "1",
             "--data-path", "data/", "--checkpoint-dir", str(PROBE_CKPT)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("NOOP_PROBE=pass reason=probe-timeout (fail-open; the real "
              "train will judge)")
        return 0
    finally:
        shutil.rmtree(PROBE_CKPT, ignore_errors=True)
    if r.returncode != 0:
        # a crashing probe is not a no-op verdict — the real train + verifier
        # own crash diagnosis, with full evidence
        print("NOOP_PROBE=pass reason=probe-train-failed (fail-open)")
        return 0

    cur = Path("predictions") / "val_preds.csv"
    if not cur.is_file():
        print("NOOP_PROBE=pass reason=no-predictions (contract breach — "
              "verify_training will handle it)")
        return 0

    if _md5(cur) == _md5(ref):
        print("NOOP_PROBE=fail — your change produced BYTE-IDENTICAL "
              "predictions to the current champion. The harness runs "
              "`python3 train.py` with NO flags: a change gated behind a new "
              "CLI flag or an unwired class never executes. Make the new "
              "behavior the DEFAULT execution path (change the default "
              "argument / replace the champion class in main()).")
        return 1
    print("NOOP_PROBE=pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
