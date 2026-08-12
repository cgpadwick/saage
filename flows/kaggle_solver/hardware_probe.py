"""Write hardware.md — the box specs perf_review checks implementations against.

Deterministic, dependency-free, tolerant: every probe that fails degrades to
"unknown"/"none" rather than failing the step. The point is that the perf
reviewer reasons against the REAL box ("you have an idle A10", "30 cores"),
not against whatever the implementing agent imagined.

Deliberately separate from prepare_comp.py's nvidia-smi probe: that one
answers "cuda or cpu for the --device flag" and honors device_override;
this one documents the raw hardware for review, even when the device was
overridden. Two different questions — keep them decoupled.
"""
import os
import shutil
import subprocess


def _ram_gb() -> str:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return f"{round(int(line.split()[1]) / 1048576)} GB"
    except (OSError, ValueError, IndexError):
        pass
    return "unknown"


def _gpus() -> list[str]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10)
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def main() -> None:
    lines = ["# hardware.md — box specs (written by hardware_probe.py; "
             "read by perf_review)", ""]
    lines.append(f"CPU cores: {os.cpu_count() or 'unknown'}")
    lines.append(f"RAM: {_ram_gb()}")
    gpus = _gpus()
    if gpus:
        lines.extend(f"GPU: {g}" for g in gpus)
    else:
        lines.append("GPU: none")
    text = "\n".join(lines) + "\n"
    with open("hardware.md", "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
