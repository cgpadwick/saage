"""First-class harness tools: file CRUD + exec + git, sandboxed to a workspace root.

Tools are provider-neutral. Each provider adapter (see llm.py) translates the
JSON-schema `parameters` to its own tool/function-calling format.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def venv_env(workspace: Path, venv: str | None) -> dict | None:
    """Return an environment dict that activates `venv`, or None if it isn't on
    disk yet. This is the deterministic equivalent of `source .../activate` for
    resolving python/pip/pytest — set on every command's subprocess so a venv
    created by an earlier step is used by all later ones, without relying on the
    agent to source anything. The existence gate means the command that *creates*
    the venv (e.g. `setup`) runs with system Python; everything after uses it.

    POSIX layout only (`<venv>/bin`); a Windows `Scripts` layout is not handled."""
    if not venv:
        return None
    vdir = Path(venv)
    if not vdir.is_absolute():
        vdir = Path(workspace) / vdir
    if not (vdir / "bin").is_dir():        # not created yet (POSIX layout)
        return None
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(vdir)
    env["PATH"] = f"{vdir / 'bin'}{os.pathsep}" + env.get("PATH", "")
    env.pop("PYTHONHOME", None)
    return env


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                      # JSON schema for the args
    fn: Callable[..., str]

    def run(self, **kwargs) -> str:
        return self.fn(**kwargs)


def _resolve(root: Path, path: str) -> Path:
    """Resolve `path` inside the workspace, rejecting escapes (`..`, abs paths out)."""
    root = root.resolve()
    p = (root / path).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"path escapes workspace: {path}")
    return p


# JSON-schema helpers
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}


def _obj(required: list[str] | None = None, **props) -> dict:
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


# --------------------------------------------------------------------------- #
# file + exec tools
# --------------------------------------------------------------------------- #
def file_tools(root: Path, venv: str | None = None) -> list[Tool]:
    root = Path(root)

    def read_file(path: str) -> str:
        return _resolve(root, path).read_text()

    def write_file(path: str, content: str) -> str:
        p = _resolve(root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {len(content)} bytes -> {path}"

    def append_file(path: str, content: str) -> str:
        p = _resolve(root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return f"appended {len(content)} bytes -> {path}"

    def edit_file(path: str, old: str, new: str) -> str:
        p = _resolve(root, path)
        s = p.read_text()
        n = s.count(old)
        if n != 1:
            raise ValueError(f"`old` must match exactly once (found {n})")
        p.write_text(s.replace(old, new))
        return f"edited {path}"

    def delete_file(path: str) -> str:
        _resolve(root, path).unlink()
        return f"deleted {path}"

    def run_command(command: str, timeout: int = 600) -> str:
        # timeout guarantees a hung command can never stall the workflow;
        # venv_env auto-activates the workspace venv once it exists
        r = subprocess.run(command, shell=True, cwd=root,
                           capture_output=True, text=True, timeout=timeout,
                           env=venv_env(root, venv))
        return (f"exit={r.returncode}\n--- stdout ---\n{r.stdout}"
                f"\n--- stderr ---\n{r.stderr}")

    return [
        Tool("read_file", "Read a UTF-8 text file from the workspace.",
             _obj(["path"], path=_STR), read_file),
        Tool("write_file", "Create or overwrite a file with content.",
             _obj(["path", "content"], path=_STR, content=_STR), write_file),
        Tool("append_file", "Append content to the end of a file (creating it if needed).",
             _obj(["path", "content"], path=_STR, content=_STR), append_file),
        Tool("edit_file", "Replace an exact substring that occurs exactly once.",
             _obj(["path", "old", "new"], path=_STR, old=_STR, new=_STR), edit_file),
        Tool("delete_file", "Delete a file from the workspace.",
             _obj(["path"], path=_STR), delete_file),
        Tool("run_command", "Run a shell command in the workspace and return output.",
             _obj(["command"], command=_STR, timeout=_INT), run_command),
    ]


# --------------------------------------------------------------------------- #
# git tools (direct git, no gh / external auth)
# --------------------------------------------------------------------------- #
def git_tools(root: Path) -> list[Tool]:
    root = Path(root)

    def _git(*args: str, timeout: int = 60) -> str:
        r = subprocess.run(["git", *args], cwd=root,
                           capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + ("\n[stderr] " + r.stderr if r.stderr else "")).strip()
        return out or "(ok)"

    def git_status() -> str:
        return _git("status", "--short", "--branch")

    def git_diff(staged: bool = False) -> str:
        return _git("diff", *(["--staged"] if staged else []))

    def git_add(paths: str) -> str:
        return _git("add", *shlex.split(paths))

    def git_commit(message: str) -> str:
        return _git("commit", "-m", message)

    def git_branch(name: str = "") -> str:
        return _git("branch", *([name] if name else []))

    def git_checkout(ref: str, create: bool = False) -> str:
        return _git("checkout", *(["-b"] if create else []), ref)

    def git_log(n: int = 10) -> str:
        return _git("log", f"-{n}", "--oneline")

    return [
        Tool("git_status", "Show working-tree status (short).",
             _obj(), git_status),
        Tool("git_diff", "Show unstaged diff (or --staged when staged=true).",
             _obj(staged=_BOOL), git_diff),
        Tool("git_add", "Stage paths (space-separated).",
             _obj(["paths"], paths=_STR), git_add),
        Tool("git_commit", "Commit staged changes with a message.",
             _obj(["message"], message=_STR), git_commit),
        Tool("git_branch", "List branches, or create one when name is given.",
             _obj(name=_STR), git_branch),
        Tool("git_checkout", "Checkout a ref, optionally creating it (create=true).",
             _obj(["ref"], ref=_STR, create=_BOOL), git_checkout),
        Tool("git_log", "Show the n most recent commits (oneline).",
             _obj(n=_INT), git_log),
    ]


def default_tools(root: Path, venv: str | None = None) -> list[Tool]:
    return file_tools(root, venv=venv) + git_tools(root)
