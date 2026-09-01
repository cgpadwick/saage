#!/usr/bin/env python3
"""Portable command deadline: run argv[2:] with a budget of argv[1] seconds.

Exits with the child's own code, or 124 (the coreutils `timeout` convention)
after killing the child's process group when the budget expires. Exists
because GNU `timeout` is absent on stock macOS, where the smoke gate would
otherwise exit 127 and route every implementation to `fail`.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys


def main() -> int:
    budget, cmd = float(sys.argv[1]), sys.argv[2:]
    kw = {"start_new_session": True} if os.name == "posix" else {}
    proc = subprocess.Popen(cmd, **kw)
    try:
        return proc.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)   # the whole test tree
            else:
                proc.kill()
        except OSError:
            pass
        proc.wait()
        print(f"[run_with_timeout] killed after {budget:g}s", file=sys.stderr)
        return 124


if __name__ == "__main__":
    sys.exit(main())
