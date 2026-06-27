"""First-class harness tools: file CRUD + exec + git, sandboxed to a workspace root.

Tools are provider-neutral. Each provider adapter (see llm.py) translates the
JSON-schema `parameters` to its own tool/function-calling format.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .shell import run_shell

if TYPE_CHECKING:
    from .config import CommandPolicy

log = logging.getLogger(__name__)


def venv_env(workspace: Path, venv: str | None) -> dict | None:
    """Return an environment dict that activates `venv`, or None if it isn't on
    disk yet. This is the deterministic equivalent of `source .../activate` for
    resolving python/pip/pytest — set on every command's subprocess so a venv
    created by an earlier step is used by all later ones, without relying on the
    agent to source anything. The existence gate means the command that *creates*
    the venv (e.g. `setup`) runs with system Python; everything after uses it.

    Handles both venv layouts: POSIX `<venv>/bin` and Windows `<venv>/Scripts`."""
    if not venv:
        return None
    vdir = Path(venv)
    if not vdir.is_absolute():
        vdir = Path(workspace) / vdir
    bindir = next((vdir / d for d in ("bin", "Scripts") if (vdir / d).is_dir()), None)
    if bindir is None:                     # not created yet
        return None
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(vdir)
    env["PATH"] = f"{bindir}{os.pathsep}" + env.get("PATH", "")
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
def file_tools(root: Path, venv: str | None = None,
               command_policy: "CommandPolicy | None" = None) -> list[Tool]:
    root = Path(root)

    def read_file(path: str) -> str:
        return _resolve(root, path).read_text(encoding="utf-8")

    # newline="\n" on every write: a flow's files must be byte-identical
    # across OSes — Windows newline translation would CRLF-corrupt e.g. a
    # bash script or byte-compared fixture an agent writes
    def write_file(path: str, content: str) -> str:
        p = _resolve(root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        return f"wrote {len(content)} bytes -> {path}"

    def append_file(path: str, content: str) -> str:
        p = _resolve(root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return f"appended {len(content)} bytes -> {path}"

    def edit_file(path: str, old: str, new: str) -> str:
        p = _resolve(root, path)
        s = p.read_text(encoding="utf-8")
        n = s.count(old)
        if n != 1:
            raise ValueError(f"`old` must match exactly once (found {n})")
        p.write_text(s.replace(old, new), encoding="utf-8", newline="\n")
        return f"edited {path}"

    def delete_file(path: str) -> str:
        _resolve(root, path).unlink()
        return f"deleted {path}"

    def run_command(command: str, timeout: int = 600) -> str:
        # refuse obviously dangerous commands BEFORE executing (non-fatal: the
        # denial is returned to the model like any other tool error)
        if command_policy is not None:
            reason = command_policy.check(command)
            if reason is not None:
                log.warning("✋ run_command refused: %s — %s", command, reason)
                return f"ERROR: {reason}; command not run"
        # timeout guarantees a hung command can never stall the workflow;
        # venv_env auto-activates the workspace venv once it exists
        r = run_shell(command, cwd=root, env=venv_env(root, venv),
                      timeout=timeout)
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
        # explicit utf-8: Windows' locale codepage can't decode UTF-8 diff bytes
        r = subprocess.run(["git", *args], cwd=root,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
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


def search_tools() -> list[Tool]:
    """The web-search tool. Keyless DuckDuckGo by default; set TAVILY_API_KEY /
    BRAVE_API_KEY for a reliable keyed backend. Opt-in per skill via `tools:`."""
    from .search import web_search
    return [
        Tool("web_search",
             "Search the web and return the top results (title, url, snippet). "
             "Use for facts, docs, or recent information beyond the workspace. "
             "max_results defaults to 5 and is clamped to [1, 20].",
             _obj(["query"], query=_STR, max_results=_INT),
             # web_search coerces/clamps max_results itself (stable ERROR contract)
             lambda query, max_results=5: web_search(query, max_results)),
    ]


def ask_user(prompt: str, *, _input=input, _isatty=None) -> str:
    """Pause and ask the human a question on the console; return the line they
    type. Returns an `ERROR:` string (never blocks / never aborts the run) when
    there's no interactive console — stdin is not a TTY (backgrounded / piped / CI),
    stdin is absent, EOF is hit, or the user cancels with Ctrl+C. (Helpers are
    injectable for testing.)"""
    if _isatty is None:
        stdin = sys.stdin
        # sys.stdin can be None (embedded / detached / closed) -> treat as non-TTY
        _isatty = stdin.isatty if hasattr(stdin, "isatty") else (lambda: False)
    if not _isatty():
        return ("ERROR: ask_user needs an interactive console, but stdin is not a "
                "TTY (this run is backgrounded / piped / non-interactive). Re-run "
                "in a terminal, or seed the value via `--set` / the shared store.")
    try:
        return _input(f"\n{prompt}\n> ").rstrip()   # trailing only; keep leading
    except (EOFError, KeyboardInterrupt):
        # Ctrl+C / EOF at the prompt dismisses it gracefully — the agent gets an
        # ERROR and the run continues, rather than KeyboardInterrupt (a
        # BaseException) escaping run_agent's `except Exception` and killing the run.
        return "ERROR: ask_user got no answer (input cancelled or end-of-input)"


def _ask_user_tool() -> Tool:
    return Tool(
        "ask_user",
        "Pause the workflow and ask the human a question on the console; returns "
        "the single line they type (after Enter). Use for confirmations, plan "
        "approval, or clarifications. In a non-interactive run it returns an ERROR "
        "instead of blocking. Note: trailing whitespace is stripped.",
        _obj(["prompt"], prompt=_STR),
        lambda prompt: ask_user(prompt))


# Opt-in tools: NOT in the default set. A skill gets one only by naming it in its
# `tools:` allow-list — so a blocking tool like ask_user can never fire in an
# autonomous flow that didn't explicitly ask for it. (name -> factory)
_OPT_IN_TOOLS = {"ask_user": _ask_user_tool}
OPT_IN_TOOL_NAMES = frozenset(_OPT_IN_TOOLS)


def opt_in_tools(names) -> list[Tool]:
    """Build the opt-in tools whose names appear in `names` (a skill's allow-list)."""
    wanted = set(names or ())
    return [make() for name, make in _OPT_IN_TOOLS.items() if name in wanted]


def default_tools(root: Path, venv: str | None = None,
                  command_policy: "CommandPolicy | None" = None) -> list[Tool]:
    return (file_tools(root, venv=venv, command_policy=command_policy)
            + git_tools(root) + search_tools())
