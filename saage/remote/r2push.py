"""Node-side artifact mirror: push the run dir's artifacts to R2/S3.

Invoked by the sidecar in start.sh (and once more by stop.sh) as

    venv/bin/python -m saage.remote.r2push

inside the run dir, with connection details in the environment (sourced from
the per-run run_env):

    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    SAAGE_R2_ENDPOINT   https://<account>.r2.cloudflarestorage.com
    SAAGE_R2_BUCKET     saage-data
    SAAGE_R2_PREFIX     runs/<run_id>

Uploads artifacts/*, status.json, saage.log, and the engine checkpoint.json.
Changed-only: a manifest tracks (size, mtime) so large files (e.g. model
checkpoints) are only re-uploaded when they actually change. Failures must
never break the run: callers invoke it with `|| true` and it exits 0 unless
misconfigured.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def plan_uploads(run_dir: Path, prefix: str, run_id: str | None = None) -> list[tuple[Path, str]]:
    """(local file, bucket key) pairs for everything worth mirroring."""
    pairs: list[tuple[Path, str]] = []
    artifacts = run_dir / "artifacts"
    if artifacts.is_dir():
        for p in sorted(artifacts.iterdir()):
            if p.is_file():
                pairs.append((p, f"{prefix}/artifacts/{p.name}"))
    for name in ("status.json", "saage.log"):
        p = run_dir / name
        if p.is_file():
            pairs.append((p, f"{prefix}/{name}"))
    run_id = run_id or os.environ.get("SAAGE_RUN_ID")
    if run_id:
        from saage.paths import runs_dir
        ck = runs_dir() / run_id / "checkpoint.json"
        if ck.is_file():
            pairs.append((ck, f"{prefix}/checkpoint.json"))
    return pairs


def _sig(p: Path) -> list:
    st = p.stat()
    return [st.st_size, int(st.st_mtime)]


def changed(pairs: list[tuple[Path, str]], manifest: Path) -> list[tuple[Path, str]]:
    """Subset of pairs whose (size, mtime) differs from the manifest."""
    try:
        seen = json.loads(manifest.read_text())
    except (OSError, ValueError):
        seen = {}
    return [(p, k) for (p, k) in pairs if seen.get(k) != _sig(p)]


def record(pairs: list[tuple[Path, str]], manifest: Path) -> None:
    try:
        seen = json.loads(manifest.read_text())
    except (OSError, ValueError):
        seen = {}
    for p, k in pairs:
        seen[k] = _sig(p)
    tmp = manifest.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen))
    tmp.replace(manifest)


def main() -> int:
    endpoint = os.environ.get("SAAGE_R2_ENDPOINT")
    bucket = os.environ.get("SAAGE_R2_BUCKET")
    prefix = os.environ.get("SAAGE_R2_PREFIX")
    if not (endpoint and bucket and prefix):
        print("r2push: SAAGE_R2_* not configured", file=sys.stderr)
        return 1
    try:
        import boto3
    except ModuleNotFoundError:
        print("r2push: boto3 not installed in this venv", file=sys.stderr)
        return 1

    client = boto3.client("s3", endpoint_url=endpoint, region_name="auto")
    run_dir = Path.cwd()
    pairs = plan_uploads(run_dir, prefix)
    manifest = run_dir / ".r2push_manifest.json"
    todo = changed(pairs, manifest)
    for local, key in todo:
        client.upload_file(str(local), bucket, key)
    record(todo, manifest)
    print(f"r2push: {len(todo)}/{len(pairs)} changed -> s3://{bucket}/{prefix}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
