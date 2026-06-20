"""`saage remote resume` — continue a killed run from its checkpoint.

In place if the original node is alive (workspace + node checkpoint intact);
on a fresh --target from the R2 mirror (checkpoint + artifacts) if the node is
gone. Same run_id throughout, so status/artifacts stay under one R2 prefix.
"""
from __future__ import annotations

import logging
import shlex
import types
from pathlib import Path

from .creds import get_target, storage_config
from .handoff import (ENGINE_EXCLUDES, HandoffError, _collect_secrets,
                      _engine_root, _load_flow)
from .scripts import RunSpec, bootstrap_sh, resume_sh, stop_sh
from .sshio import SSHError
from .state import find_run
from .target import SshTarget

log = logging.getLogger("saage.remote")


class ResumeError(RuntimeError):
    pass


def decide(*, node_alive: bool, session_running: bool, have_target: bool) -> str:
    if session_running:
        raise ResumeError("run is still active (tmux session alive) — "
                          "`saage remote kill` it first if you mean to restart")
    if node_alive:
        return "in_place"
    if not have_target:
        raise ResumeError("original node is unreachable — pass --target <name> to "
                          "resume on a fresh box from the R2 checkpoint")
    return "cross_box"


def _ws_view(manifest: dict) -> types.SimpleNamespace:
    """A minimal WorkspacePlan-like object exposing the attributes
    `_collect_secrets` reads, rebuilt from the manifest's `workspace` block.
    Ephemeral runs (no workspace block) yield an all-None view."""
    ws = manifest.get("workspace") or {}
    return types.SimpleNamespace(
        mode=ws.get("mode", "ephemeral"),
        run_branch=ws.get("run_branch"),
        repo_url=ws.get("repo"),
        base_sha=ws.get("base_sha"),
        bundle=None,
        workspace=None,
    )


def resume_run(run_ref: str | None, *, target_name: str | None = None) -> "RunState":
    rs = find_run(run_ref)
    manifest = rs.manifest()
    if not manifest:
        raise ResumeError(f"run {rs.run_id} has no manifest — cannot resume "
                          f"(was it handed off by this saage?)")
    flow_path = Path(manifest["flow"])
    flow_doc = _load_flow(flow_path)
    provider_type = manifest.get("provider", "")

    # -- probe the original node ----------------------------------------------
    orig = SshTarget(get_target(manifest["target"]))
    try:
        node_alive = orig.conn.ok("true")
    except SSHError:
        node_alive = False
    session_running = node_alive and orig.session_alive(rs.run_id)

    mode = decide(node_alive=node_alive, session_running=session_running,
                  have_target=bool(target_name))

    # -- rebuild the RunSpec (same run_id, same flow + artifacts) --------------
    ws_view = _ws_view(manifest)
    storage = storage_config()
    spec = RunSpec(
        run_id=rs.run_id,
        flow_file=flow_path.name,
        ws_mode=ws_view.mode,
        set_args=manifest.get("set") or {},
        r2=storage is not None,
        ws_setup=manifest.get("ws_setup"),
        artifacts=tuple(manifest.get("artifacts") or ()),
    )
    secrets = _collect_secrets(provider_type, ws_view, {}, rs.run_id, storage)
    env_text = "".join(f"{k}={shlex.quote(v)}\n" for k, v in secrets.items())

    if mode == "in_place":
        node = orig
        rdir = node.run_dir(rs.run_id)
        conn = node.conn
        conn.run(f"mkdir -p $HOME/{rdir}/artifacts")
        # re-push engine + flow if missing (a node may have been pruned of the
        # source while keeping ws/ + the checkpoint); cheap rsync no-ops if present
        conn.rsync_to(_engine_root().as_posix() + "/", f"{rdir}/saage/",
                      excludes=ENGINE_EXCLUDES, delete=True)
        conn.rsync_to(flow_path.parent.as_posix() + "/", f"{rdir}/flow/",
                      delete=True, excludes=("__pycache__",))
        conn.write_file(f"{rdir}/run_env", env_text)
        conn.write_file(f"{rdir}/resume.sh", resume_sh(spec), mode="700")
        (rs.dir / "resume.sh").write_text(resume_sh(spec), newline="\n")
        node.start_script(rs.run_id, "resume.sh")

    else:  # cross_box
        if storage is None:
            raise ResumeError(
                "cross-box resume needs an R2 mirror (the new box restores the "
                "checkpoint + artifacts from R2) but no [storage] is configured")
        if ws_view.mode == "bundle":
            raise ResumeError(
                "cross-box resume needs a pushed run branch; this run used bundle "
                "mode (no remote repo) so its workspace can't be reconstructed on "
                "a new box — resume in place on the original node")
        node = SshTarget(get_target(target_name))
        node.preflight()
        conn = node.conn
        rdir = node.run_dir(rs.run_id)
        conn.run(f"mkdir -p $HOME/{rdir}/artifacts")
        log.info("pushing engine source + flow dir to %s", conn.dest)
        conn.rsync_to(_engine_root().as_posix() + "/", f"{rdir}/saage/",
                      excludes=ENGINE_EXCLUDES, delete=True)
        conn.rsync_to(flow_path.parent.as_posix() + "/", f"{rdir}/flow/",
                      delete=True, excludes=("__pycache__",))
        conn.write_file(f"{rdir}/run_env", env_text)
        for name, content in (("bootstrap.sh", bootstrap_sh(spec)),
                              ("resume.sh", resume_sh(spec)),
                              ("stop.sh", stop_sh(spec))):
            (rs.dir / name).write_text(content, newline="\n")
            conn.write_file(f"{rdir}/{name}", content, mode="700")

        # bootstrap: deps + workspace clone (run branch) + optional ws_setup
        log.info("bootstrapping fresh node (deps + workspace clone)")
        proc = conn.run(f"bash $HOME/{rdir}/bootstrap.sh", timeout=1800, check=False)
        (rs.dir / "resume_bootstrap.log").write_text(proc.stdout + proc.stderr)
        if proc.returncode != 0 or "BOOTSTRAP_OK" not in proc.stdout:
            rs.update(phase="failed")
            rs.event("resume_bootstrap_failed", rc=proc.returncode)
            conn.run(f"rm -f $HOME/{rdir}/run_env", check=False)
            raise ResumeError(
                f"resume bootstrap failed (rc={proc.returncode}) — see "
                f"{rs.dir / 'resume_bootstrap.log'}\n"
                f"{(proc.stderr or proc.stdout)[-2000:]}")

        # restore checkpoint + artifacts from R2 onto the fresh box
        r2pull_proc = conn.run(
            f"cd $HOME/{rdir} && set -a; source ./run_env; set +a; "
            f"venv/bin/python -m saage.remote.r2pull "
            f"--run-id {shlex.quote(rs.run_id)} --run-dir $PWD",
            timeout=900, check=False)
        if r2pull_proc.returncode != 0:
            raise ResumeError(
                f"no checkpoint found in R2 for run {rs.run_id} "
                f"(r2pull failed) — cannot resume on a fresh box")

        # stage restored artifacts (the kept best model etc.) into ws/ at the
        # flow's declared artifact paths, so the resumed engine sees them in place
        _stage_restored(conn, rdir, spec.artifacts)

        node.start_script(rs.run_id, "resume.sh")

    rs.update(phase="resuming", target=node.target.name)
    rs.event("resume", mode=mode)
    log.info("run %s resuming (%s) on %s", rs.run_id, mode, node.target.name)
    return rs


def _stage_restored(conn, rdir: str, artifacts: tuple[str, ...]) -> None:
    """Copy each restored_artifacts/<name> file back to its declared
    workspace-relative path under ws/, so the resumed flow finds it where it
    left it. Names that are globs (no fixed basename) are best-effort: a glob's
    files were saved flat in restored_artifacts/ and copied to ws/<dir>/."""
    for pat in artifacts:
        # the artifact declaration is a ws-relative path/glob; r2pull saved each
        # file flat by basename. Copy by basename into the pattern's directory.
        dirn = str(Path(pat).parent)
        target_dir = f"ws/{dirn}" if dirn not in (".", "") else "ws"
        base = Path(pat).name
        conn.run(
            f"cd $HOME/{rdir} && mkdir -p {shlex.quote(target_dir)} && "
            f"for f in restored_artifacts/{base}; do [ -f \"$f\" ] && "
            f"cp -f \"$f\" {shlex.quote(target_dir)}/ ; done",
            check=False)
