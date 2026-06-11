"""Workspace packaging: ship a git ref, not files.

Brownfield flows point at an existing repo that the flow mutates
(`workspace: /home/.../le-wm`). The node-side copy must be a real git repo
(keep_or_revert et al. depend on git), so packaging means:

  * create a run branch `saage-run-<run_id>` at the handoff point
  * pushable `origin` -> push the branch; the node clones it   (mode: branch)
  * no remote          -> `git bundle` the branch; node clones the bundle
                          (mode: bundle — the repo travels *as a repo*)
  * dirty working tree -> never silently ship HEAD: either abort (default) or
    snapshot the full working tree (tracked changes + untracked files) into a
    commit on the run branch via a temporary index — the user's checkout,
    index, and HEAD are never touched.

Greenfield flows (workspace missing or not a git repo) are mode "ephemeral":
the node just gets a fresh directory.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_GIT_ID = ["-c", "user.email=saage@local", "-c", "user.name=saage"]


class WorkspaceError(RuntimeError):
    pass


class DirtyWorkspace(WorkspaceError):
    pass


@dataclass
class WorkspacePlan:
    mode: str                       # 'ephemeral' | 'branch' | 'bundle'
    workspace: Path | None = None   # local workspace dir (package modes)
    run_branch: str | None = None
    base_sha: str | None = None
    tip_sha: str | None = None      # == base_sha unless a dirty snapshot was committed
    repo_url: str | None = None     # mode 'branch'
    bundle: Path | None = None      # mode 'bundle': local path to the bundle file
    dirty_tree: str = "clean"       # 'clean' | 'committed'


def _git(ws: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ws), *args],
                          capture_output=True, text=True, check=check)


def is_git_repo(d: Path) -> bool:
    if not d.is_dir():
        return False
    proc = _git(d, "rev-parse", "--is-inside-work-tree", check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _has_commits(ws: Path) -> bool:
    return _git(ws, "rev-parse", "--verify", "HEAD", check=False).returncode == 0


def dirty_paths(ws: Path) -> list[str]:
    out = _git(ws, "status", "--porcelain").stdout
    return [line for line in out.splitlines() if line.strip()]


def snapshot_commit(ws: Path) -> str:
    """Commit the working tree (tracked changes + untracked) WITHOUT touching
    the user's index, HEAD, or checkout, via a temporary index file."""
    head = _git(ws, "rev-parse", "HEAD").stdout.strip()
    with tempfile.NamedTemporaryFile(prefix="saage-index-") as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": tmp.name}

        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(ws), *_GIT_ID, *args],
                                  capture_output=True, text=True, check=True,
                                  env=env).stdout.strip()

        git("read-tree", head)
        git("add", "-A")
        tree = git("write-tree")
        return git("commit-tree", tree, "-p", head, "-m", "saage: handoff snapshot")


def plan_workspace(workspace: Path | None, run_id: str, *, mode: str = "auto",
                   dirty: str = "abort", out_dir: Path | None = None) -> WorkspacePlan:
    """Decide how the workspace travels and create the run branch (+ bundle).

    mode:  auto      — package iff workspace is an existing git repo with commits
           ephemeral — fresh dir on the node, never package
           package   — require a packageable repo, error otherwise
    dirty: abort | commit  (what to do with uncommitted changes)
    out_dir: where a bundle file gets written (the run's state dir).
    """
    packageable = workspace is not None and is_git_repo(workspace) and _has_commits(workspace)
    if mode == "ephemeral" or (mode == "auto" and not packageable):
        return WorkspacePlan(mode="ephemeral")
    if not packageable:
        raise WorkspaceError(
            f"--workspace-mode package, but {workspace} is not a git repo with commits"
        )

    assert workspace is not None
    base_sha = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    tip_sha, dirty_state = base_sha, "clean"

    dirt = dirty_paths(workspace)
    if dirt:
        if dirty == "abort":
            raise DirtyWorkspace(
                f"workspace {workspace} has uncommitted changes ({len(dirt)} paths). "
                f"Re-run with --dirty commit to snapshot them onto the run branch, "
                f"--dirty ship-head to package HEAD as-is (ignoring local edits — "
                f"right when something else is actively using the workspace), "
                f"or commit/stash them yourself first."
            )
        if dirty == "commit":
            tip_sha, dirty_state = snapshot_commit(workspace), "committed"
        else:                       # ship-head: explicit choice to ignore the dirt
            dirty_state = "ignored-ship-head"

    run_branch = f"saage-run-{run_id}"
    _git(workspace, "branch", run_branch, tip_sha)

    origin = _git(workspace, "remote", "get-url", "origin", check=False)
    if origin.returncode == 0:
        push = _git(workspace, "push", "origin", run_branch, check=False)
        if push.returncode == 0:
            return WorkspacePlan(mode="branch", workspace=workspace,
                                 run_branch=run_branch, base_sha=base_sha,
                                 tip_sha=tip_sha, repo_url=origin.stdout.strip(),
                                 dirty_tree=dirty_state)
        # push failed (no auth, offline, read-only remote) -> bundle fallback

    if out_dir is None:
        raise WorkspaceError("bundle mode needs out_dir for the bundle file")
    bundle = out_dir / "ws.bundle"
    _git(workspace, "bundle", "create", str(bundle), run_branch)
    return WorkspacePlan(mode="bundle", workspace=workspace, run_branch=run_branch,
                         base_sha=base_sha, tip_sha=tip_sha, bundle=bundle,
                         dirty_tree=dirty_state)
