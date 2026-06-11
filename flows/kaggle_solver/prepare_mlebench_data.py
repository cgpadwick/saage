#!/usr/bin/env python3
"""Prepare mle-bench competition data when your Kaggle creds are KGAT tokens.

mlebench pins `kaggle<1.7`, which predates Kaggle's access tokens (KGAT_*) —
its built-in downloader can only authenticate with classic kaggle.json
username/key pairs. This driver implements the documented workaround
(docs/kaggle_solver_plan.md §4): download the zip yourself with kaggle>=2.2,
then run mlebench's prepare with its downloader stubbed to the pre-placed zip.

Usage (laptop-side, one-time per competition; needs python >= 3.11):

    export KAGGLE_API_TOKEN=$(cat ~/.kaggle/access_token)
    uvx --python 3.12 --from kaggle==2.2.1 kaggle competitions download \
        -c <comp-id> -p <data-dir>/<comp-id>/
    uv run --python 3.12 \
        --with "mlebench @ git+https://github.com/openai/mle-bench.git" \
        python prepare_mlebench_data.py --comp <comp-id> --data-dir <data-dir>

Rules note: the kaggle 2.x download step fails with 403 until you have
accepted the competition's rules on kaggle.com.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--comp", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--skip-verification", action="store_true",
                    help="skip the zip checksum check")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    zip_path = data_dir / args.comp / f"{args.comp}.zip"
    if not zip_path.exists():
        print(f"ERROR: {zip_path} not found — download it first with kaggle>=2.2 "
              f"(see module docstring)", file=sys.stderr)
        sys.exit(1)

    import mlebench.data as data

    def pre_placed_zip(competition_id, download_dir, quiet=False, force=False):
        z = Path(download_dir) / f"{competition_id}.zip"
        if not z.exists():
            raise FileNotFoundError(f"pre-place the zip at {z}")
        print(f"using pre-placed zip: {z}")
        return z

    data.download_dataset = pre_placed_zip          # bypass kaggle<1.7 auth

    from mlebench.registry import registry
    comp = registry.set_data_dir(data_dir).get_competition(args.comp)
    data.download_and_prepare_dataset(
        comp, skip_verification=args.skip_verification)

    public = comp.public_dir
    print(f"prepared: {public}")
    for p in sorted(public.iterdir()):
        print(f"  {p.name}")
    print("PREPARE=ok")


if __name__ == "__main__":
    main()
