#!/usr/bin/env python3
"""Stage an mle-bench competition into the flow workspace (deterministic — no LLM).

Port of mle-beast's `benchmark/competition.py` (load_competition,
build_task_description) plus the workspace-staging steps of
`benchmark/runner.run_single_competition`. Runs with cwd = the workspace.

  1. sanity-checks the prepared competition data exists (and fails fast with
     the exact fix when it doesn't — mlebench install, kaggle creds, prepare),
  2. links the competition's prepared/public data to ./data (copy on
     platforms without symlinks),
  3. copies sample_submission.csv + description.md into the workspace root,
  4. writes task.md — the competition context every skill reads,
  5. detects the training device.

Prints `SAMPLE_COLS=... SAMPLE_ROWS=... DEVICE=... TASK_READY=ok` for `set:`
captures.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path

# the mle-bench "Lite" (low-difficulty) subset — what `mlebench prepare --lite`
# downloads, and what the sweep driver iterates
LITE_COMPETITION_IDS = [
    "aerial-cactus-identification",
    "aptos2019-blindness-detection",
    "denoising-dirty-documents",
    "dog-breed-identification",
    "dogs-vs-cats-redux-kernels-edition",
    "histopathologic-cancer-detection",
    "jigsaw-toxic-comment-classification-challenge",
    "leaf-classification",
    "mlsp-2013-birds",
    "new-york-city-taxi-fare-prediction",
    "nomad2018-predict-transparent-conductors",
    "plant-pathology-2020-fgvc7",
    "random-acts-of-pizza",
    "ranzcr-clip-catheter-line-classification",
    "siim-isic-melanoma-classification",
    "spooky-author-identification",
    "tabular-playground-series-dec-2021",
    "tabular-playground-series-may-2022",
    "text-normalization-challenge-english-language",
    "text-normalization-challenge-russian-language",
]

MISSING_DATA_HELP = """\
ERROR: prepared competition data not found at {base}

To prepare it (one-time, needs ~py3.11+):
  pip install "mlebench @ git+https://github.com/openai/mle-bench.git"
  export KAGGLE_API_TOKEN=$(cat ~/.kaggle/access_token)   # kaggle>=2.2 token
  mlebench prepare -c {comp} --data-dir {data_dir}
Notes:
  - the kaggle package must be >=2.2 (python >=3.11) for KGAT access tokens;
    classic kaggle.json username/key pairs also work
  - you must have ACCEPTED THE COMPETITION RULES on kaggle.com for {comp}
    (visit the competition page -> Rules -> I Understand and Accept)
"""


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def detect_device(force: str | None) -> str:
    if force:
        return force
    try:
        ok = subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0
    except (FileNotFoundError, OSError):   # no nvidia-smi binary (CPU-only host/CI)
        ok = False
    return "cuda" if ok else "cpu"


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--comp", required=True)
    ap.add_argument("--data-dir", required=True,
                    help="mle-bench prepared data root (mlebench prepare --data-dir)")
    ap.add_argument("--device", default="",
                    help="cuda|cpu (default: auto via nvidia-smi)")
    args = ap.parse_args()

    base = Path(args.data_dir).expanduser() / args.comp
    public = base / "prepared" / "public"
    if not public.is_dir():
        print(MISSING_DATA_HELP.format(base=base, comp=args.comp,
                                       data_dir=args.data_dir), file=sys.stderr)
        sys.exit(1)

    # -- description -----------------------------------------------------------
    desc_path = base / "description.md"
    if not desc_path.exists():
        desc_path = public / "description.md"
    description = (desc_path.read_text(encoding="utf-8") if desc_path.exists()
                   else f"Competition: {args.comp} (no description.md found)")
    Path("description.md").write_text(description, encoding="utf-8")

    # -- data link --------------------------------------------------------------
    data = Path("data")
    if data.is_symlink() or data.is_file():
        data.unlink()
    elif data.is_dir():
        shutil.rmtree(data)
    try:
        data.symlink_to(public.resolve(), target_is_directory=True)
    except OSError:                                   # no symlink permission
        shutil.copytree(public, data)

    # -- sample submission -> expected contract ---------------------------------
    sample = public / "sample_submission.csv"
    columns: list[str] = []
    rows = 0
    if sample.exists():
        shutil.copy2(sample, "sample_submission.csv")
        with open(sample, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            columns = next(reader, None) or []
            rows = sum(1 for _ in reader)

    # -- task.md (port of build_task_description) -------------------------------
    parts = ["# Competition Description\n", description.strip(),
             "\n\n# Data Files\n", "Data directory: data/\n"]
    for entry in sorted(public.iterdir()):
        if entry.is_file():
            parts.append(f"  - {entry.name} ({fmt_size(entry.stat().st_size)})")
    parts += ["\n\n# Submission Format\n"]
    if columns:
        parts.append(f"Columns: {', '.join(columns)}")
    parts += [f"Expected rows: {rows}",
              "Your final output MUST be a file called submission.csv "
              "with exactly these columns and this number of rows."]
    Path("task.md").write_text("\n".join(parts), encoding="utf-8")

    device = detect_device(args.device or None)
    print(f"SAMPLE_COLS={','.join(columns)}")
    print(f"SAMPLE_ROWS={rows}")
    print(f"DEVICE={device}")
    print("TASK_READY=ok")


if __name__ == "__main__":
    main()
