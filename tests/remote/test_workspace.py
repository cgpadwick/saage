import subprocess
from pathlib import Path

import pytest

from saage.remote.workspace import (DirtyWorkspace, WorkspaceError,
                                    dirty_paths, plan_workspace)

from .conftest import git


def test_auto_is_ephemeral_when_dir_missing(tmp_path):
    plan = plan_workspace(tmp_path / "nope", "r1", out_dir=tmp_path)
    assert plan.mode == "ephemeral"


def test_auto_is_ephemeral_when_not_a_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    plan = plan_workspace(d, "r1", out_dir=tmp_path)
    assert plan.mode == "ephemeral"


def test_package_mode_requires_a_repo(tmp_path):
    with pytest.raises(WorkspaceError, match="not a git repo"):
        plan_workspace(tmp_path / "nope", "r1", mode="package", out_dir=tmp_path)


def test_clean_repo_no_remote_bundles(ws_repo, tmp_path):
    plan = plan_workspace(ws_repo, "r1", out_dir=tmp_path)
    assert plan.mode == "bundle"
    assert plan.run_branch == "saage-run-r1"
    assert plan.dirty_tree == "clean"
    assert plan.bundle is not None and plan.bundle.exists()
    heads = subprocess.run(["git", "bundle", "list-heads", str(plan.bundle)],
                           capture_output=True, text=True, check=True).stdout
    assert "refs/heads/saage-run-r1" in heads
    # the bundle is a clonable repo (what the node-side bootstrap does)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--branch", "saage-run-r1",
                    str(plan.bundle), str(clone)], check=True)
    assert (clone / "train.py").exists()


def test_repo_with_pushable_origin_uses_branch_mode(ws_repo, tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(ws_repo, "remote", "add", "origin", str(bare))

    plan = plan_workspace(ws_repo, "r2", out_dir=tmp_path)
    assert plan.mode == "branch"
    assert plan.repo_url == str(bare)
    in_origin = subprocess.run(["git", "-C", str(bare), "branch", "--list"],
                               capture_output=True, text=True).stdout
    assert "saage-run-r2" in in_origin


def test_unpushable_origin_falls_back_to_bundle(ws_repo, tmp_path):
    git(ws_repo, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
    plan = plan_workspace(ws_repo, "r3", out_dir=tmp_path)
    assert plan.mode == "bundle"
    assert plan.bundle.exists()


def test_dirty_abort_is_the_default(ws_repo, tmp_path):
    (ws_repo / "config.yaml").write_text("epochs: 16\n")
    with pytest.raises(DirtyWorkspace, match="uncommitted changes"):
        plan_workspace(ws_repo, "r4", out_dir=tmp_path)


def test_dirty_ship_head_packages_head_and_ignores_edits(ws_repo, tmp_path):
    (ws_repo / "config.yaml").write_text("epochs: 16\n")          # local edit, mid-use
    head = git(ws_repo, "rev-parse", "HEAD").stdout.strip()

    plan = plan_workspace(ws_repo, "r6", dirty="ship-head", out_dir=tmp_path)

    assert plan.dirty_tree == "ignored-ship-head"
    assert plan.tip_sha == head                                   # HEAD, not a snapshot
    assert git(ws_repo, "rev-parse", plan.run_branch).stdout.strip() == head
    # the local edit is untouched and NOT in the shipped tree
    assert (ws_repo / "config.yaml").read_text() == "epochs: 16\n"
    assert "epochs: 8" in git(ws_repo, "show", f"{plan.tip_sha}:config.yaml").stdout


def test_dirty_commit_snapshots_without_touching_checkout(ws_repo, tmp_path):
    (ws_repo / "config.yaml").write_text("epochs: 16\n")          # tracked, modified
    (ws_repo / "new_module.py").write_text("x = 1\n")             # untracked
    head_before = git(ws_repo, "rev-parse", "HEAD").stdout.strip()
    dirt_before = dirty_paths(ws_repo)

    plan = plan_workspace(ws_repo, "r5", dirty="commit", out_dir=tmp_path)

    assert plan.dirty_tree == "committed"
    assert plan.base_sha == head_before
    assert plan.tip_sha != head_before
    # user's checkout, HEAD, and index are untouched
    assert git(ws_repo, "rev-parse", "HEAD").stdout.strip() == head_before
    assert dirty_paths(ws_repo) == dirt_before
    # the snapshot commit contains both the tracked change and the untracked file
    shown = git(ws_repo, "show", f"{plan.tip_sha}:config.yaml").stdout
    assert "epochs: 16" in shown
    assert git(ws_repo, "show", f"{plan.tip_sha}:new_module.py").stdout == "x = 1\n"
    # and the run branch points at the snapshot
    assert git(ws_repo, "rev-parse", plan.run_branch).stdout.strip() == plan.tip_sha
