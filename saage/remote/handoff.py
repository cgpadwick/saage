"""The button: package -> push -> bootstrap -> start -> disconnect.

After `handoff()` returns, the local machine is optional: the engine runs on
the node under tmux, heartbeats status.json, and copies ledgers into artifacts/.
"""
from __future__ import annotations

import logging
import os
import re
import secrets as pysecrets
import shlex
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .creds import PROVIDER_ENV, Storage, Target, storage_config
from .scripts import ARTIFACT_FILES, RunSpec, bootstrap_sh, start_sh, stop_sh
from .state import RunState
from .target import PreflightError, SshTarget
from .workspace import WorkspacePlan, plan_workspace

log = logging.getLogger("saage.remote")

# what rsync skips when shipping the engine source — runtime state and bulk
# that `pip install -e` doesn't need (README.md/LICENSE must travel: the build
# backend requires them)
ENGINE_EXCLUDES = (".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                   "*.log", ".github", ".claude", "docs", "tests", "flows")


class HandoffError(RuntimeError):
    pass


def _engine_root() -> Path:
    # saage/remote/handoff.py -> saage/remote -> saage -> repo root
    return Path(__file__).resolve().parents[2]


def _gen_run_id(flow_dir_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"{flow_dir_name}-{stamp}-{pysecrets.token_hex(2)}"


def _load_flow(flow_path: Path) -> dict:
    if not flow_path.is_file():
        raise HandoffError(f"flow file not found: {flow_path}")
    return yaml.safe_load(flow_path.read_text()) or {}


# workspace-relative filenames/globs only — these land unquoted in a generated
# bash loop, so anything outside this charset is refused, not escaped
_ARTIFACT_PATTERN = re.compile(r"^[\w.\-*?/\[\]]+$")


def _flow_artifacts(flow_doc: dict) -> tuple[str, ...]:
    """The flow's `artifacts:` declaration (filenames/globs the sidecar collects
    from the workspace), or the default ledger/report names. Ignored by local
    runs, like `workspace:`."""
    arts = flow_doc.get("artifacts")
    if arts is None:
        return ARTIFACT_FILES
    if not isinstance(arts, list) or not all(isinstance(a, str) and a for a in arts):
        raise HandoffError(
            "flow.yaml `artifacts:` must be a list of filename/glob strings")
    for a in arts:
        if a.startswith("/") or ".." in a or not _ARTIFACT_PATTERN.match(a):
            raise HandoffError(
                f"flow.yaml `artifacts:` pattern {a!r} not supported — "
                f"workspace-relative filenames/globs only")
    return tuple(arts)


def _collect_secrets(provider_type: str, ws_plan: WorkspacePlan,
                     extra_env: dict[str, str], run_id: str,
                     storage: Storage | None) -> dict[str, str]:
    """Everything that lands in the node's run_env (0600, deleted at run end)."""
    env: dict[str, str] = {"SAAGE_RUN_ID": run_id}
    if storage:
        env.update({
            "AWS_ACCESS_KEY_ID": storage.access_key,
            "AWS_SECRET_ACCESS_KEY": storage.secret_key,
            "SAAGE_R2_ENDPOINT": storage.endpoint,
            "SAAGE_R2_BUCKET": storage.bucket,
            "SAAGE_R2_PREFIX": storage.run_prefix(run_id),
        })
    var = PROVIDER_ENV.get(provider_type)
    if var:
        value = os.environ.get(var)
        if value:
            env[var] = value
        elif provider_type != "local":
            raise HandoffError(
                f"flow uses provider {provider_type!r} but {var} is not set in "
                f"this environment — the node needs a key to run agent steps"
            )
    if ws_plan.run_branch:
        env["WS_RUN_BRANCH"] = ws_plan.run_branch
    if ws_plan.repo_url:
        env["WS_REPO_URL"] = ws_plan.repo_url
        token = os.environ.get("SAAGE_GIT_TOKEN")
        if token and ws_plan.repo_url.startswith("https://"):
            # the token-bearing URL lives ONLY in run_env (0600, deleted when
            # the run stops) and is used per-operation; the clean URL is what
            # lands in the workspace's .git/config — a PAT baked into the
            # clone's origin would outlive the run (run dirs aren't cleaned)
            env["WS_REPO_AUTH_URL"] = ws_plan.repo_url.replace(
                "https://", f"https://x-access-token:{token}@", 1)
    env.update(extra_env)
    return env


def handoff(*, flow: str, target: Target, set_args: dict | None = None,
            extra_env: dict[str, str] | None = None, workspace_mode: str = "auto",
            dirty: str = "abort", max_run_days: float = 12.0,
            sync_interval: int = 300, need_gpu: bool = False,
            ws_setup: str | None = None, bootstrap_timeout: int = 1800) -> RunState:
    flow_path = Path(flow).resolve()
    flow_doc = _load_flow(flow_path)
    flow_dir = flow_path.parent
    provider_type = (flow_doc.get("provider") or {}).get("type", "")
    declared_ws = flow_doc.get("workspace")
    venv_arg = flow_doc.get("venv")            # engine default applies if None

    run_id = _gen_run_id(flow_dir.name)
    rs = RunState.create(run_id)
    rs.event("handoff_started", flow=str(flow_path), target=target.name)

    # -- preflight: fail before anything is touched ---------------------------
    node = SshTarget(target)
    for warning in node.preflight(need_gpu=need_gpu):
        log.warning(warning)
    ws_plan = plan_workspace(
        Path(declared_ws).expanduser() if declared_ws else None,
        run_id, mode=workspace_mode, dirty=dirty, out_dir=rs.dir,
    )
    storage = storage_config()
    secrets = _collect_secrets(provider_type, ws_plan, extra_env or {}, run_id, storage)
    rs.event("preflight_ok", ws_mode=ws_plan.mode, dirty_tree=ws_plan.dirty_tree,
             r2=bool(storage))

    # -- record intent ---------------------------------------------------------
    spec = RunSpec(run_id=run_id, flow_file=flow_path.name, ws_mode=ws_plan.mode,
                   set_args=set_args or {}, venv_arg=venv_arg,
                   sync_interval=sync_interval, max_run_days=max_run_days,
                   r2=storage is not None, ws_setup=ws_setup,
                   artifacts=_flow_artifacts(flow_doc))
    rs.write_manifest({
        "run_id": run_id,
        "flow": str(flow_path),
        "target": target.name,
        "set": spec.set_args,
        "provider": provider_type,
        "workspace": None if ws_plan.mode == "ephemeral" else {
            "dir": str(ws_plan.workspace),
            "mode": ws_plan.mode,
            "repo": ws_plan.repo_url,
            "base_sha": ws_plan.base_sha,
            "run_branch": ws_plan.run_branch,
            "dirty_tree": ws_plan.dirty_tree,
        },
        "secrets_pushed": sorted(secrets),     # names only, never values
        "bucket": f"s3://{storage.bucket}/{storage.run_prefix(run_id)}" if storage else None,
        "ws_setup": ws_setup,
        "artifacts": list(spec.artifacts),
    })
    rs.update(phase="pushing", target=target.name,
              node={"host": target.host, "user": target.user,
                    "hourly_usd": target.hourly_usd},
              tmux_session=spec.session,
              started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    # -- push ---------------------------------------------------------------
    conn = node.conn
    rdir = node.run_dir(run_id)
    conn.run(f"mkdir -p $HOME/{rdir}/artifacts")
    log.info("pushing engine source + flow dir to %s", conn.dest)
    conn.rsync_to(_engine_root().as_posix() + "/", f"{rdir}/saage/",
                  excludes=ENGINE_EXCLUDES, delete=True)
    conn.rsync_to(flow_dir.as_posix() + "/", f"{rdir}/flow/", delete=True,
                  excludes=("__pycache__",))
    if ws_plan.bundle:
        conn.rsync_to(ws_plan.bundle, f"{rdir}/ws.bundle")
    env_text = "".join(f"{k}={shlex.quote(v)}\n" for k, v in secrets.items())
    conn.write_file(f"{rdir}/run_env", env_text)        # home-relative: write_file quotes
    for name, content in (("bootstrap.sh", bootstrap_sh(spec)),
                          ("start.sh", start_sh(spec)),
                          ("stop.sh", stop_sh(spec))):
        (rs.dir / name).write_text(content)      # local copy, for debugging
        conn.write_file(f"{rdir}/{name}", content, mode="700")
    rs.event("pushed")

    # -- bootstrap (deps + workspace clone; minutes, streamed to handoff.log) --
    log.info("bootstrapping node (deps + workspace) — this can take a few minutes")
    rs.update(phase="bootstrapping")
    proc = conn.run(f"bash $HOME/{rdir}/bootstrap.sh", timeout=bootstrap_timeout,
                    check=False)
    (rs.dir / "handoff.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0 or "BOOTSTRAP_OK" not in proc.stdout:
        rs.update(phase="failed")
        rs.event("bootstrap_failed", rc=proc.returncode)
        conn.run(f"rm -f $HOME/{rdir}/run_env", check=False)   # never leave secrets behind
        raise HandoffError(
            f"bootstrap failed (rc={proc.returncode}) — see {rs.dir / 'handoff.log'}\n"
            f"{(proc.stderr or proc.stdout)[-2000:]}"
        )
    rs.event("bootstrap_ok")

    # -- start, detached ---------------------------------------------------------
    node.start(run_id)
    rs.update(phase="running")
    rs.event("started", session=spec.session)
    log.info("run %s handed off to %s — `saage remote status`", run_id, target.name)
    return rs
