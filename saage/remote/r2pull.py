#!/usr/bin/env python3
"""Node-side restore: pull a run's checkpoint + artifacts from R2/S3.

Used by cross-box `saage remote resume` to seed a fresh box before
`saage resume`. Same SAAGE_R2_* env as r2push (sourced from run_env).

    python -m saage.remote.r2pull --run-id <id> --run-dir <dir>

Checkpoint -> ~/.saage/runs/<id>/checkpoint.json (where `saage resume` reads it).
Artifacts  -> <run-dir>/restored_artifacts/<name> (the resume flow stages the
best model from here to its workspace path).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def plan_downloads(prefix: str, names: list[str]) -> list[tuple[str, str]]:
    """(bucket key, kind) for each name; kind is 'checkpoint' or 'artifact'."""
    out: list[tuple[str, str]] = []
    for n in names:
        kind = "checkpoint" if n == "checkpoint.json" else "artifact"
        out.append((f"{prefix}/{n}", kind))
    return out


def _client(endpoint: str):
    import boto3
    return boto3.client("s3", endpoint_url=endpoint, region_name="auto")


def main() -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    endpoint = os.environ.get("SAAGE_R2_ENDPOINT")
    bucket = os.environ.get("SAAGE_R2_BUCKET")
    prefix = os.environ.get("SAAGE_R2_PREFIX")
    if not (endpoint and bucket and prefix):
        print("r2pull: SAAGE_R2_* not configured", file=sys.stderr)
        return 1
    try:
        client = _client(endpoint)
    except ModuleNotFoundError:
        print("r2pull: boto3 not installed", file=sys.stderr)
        return 1

    from saage.paths import runs_dir
    ck_dir = runs_dir() / args.run_id
    ck_dir.mkdir(parents=True, exist_ok=True)
    restored = Path(args.run_dir) / "restored_artifacts"
    restored.mkdir(parents=True, exist_ok=True)

    # list artifact keys under the prefix
    names = ["checkpoint.json"]
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/artifacts/")
    except Exception as exc:
        print(f"r2pull: list failed: {exc}", file=sys.stderr)
        return 1
    for obj in resp.get("Contents", []):
        names.append("artifacts/" + obj["Key"].rsplit("/", 1)[-1])

    pulled = 0
    for key, kind in plan_downloads(prefix, names):
        dest = (ck_dir / "checkpoint.json") if kind == "checkpoint" \
            else (restored / key.rsplit("/", 1)[-1])
        try:
            client.download_file(bucket, key, str(dest))
            pulled += 1
        except Exception as exc:                  # missing object is non-fatal
            print(f"r2pull: skip {key}: {exc}", file=sys.stderr)
    print(f"r2pull: restored {pulled} object(s) from s3://{bucket}/{prefix}/")
    return 0 if (ck_dir / "checkpoint.json").is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
