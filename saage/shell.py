"""Run flow-command strings with POSIX `sh` semantics on every OS.

The engine's command language — `command:` steps and the agent's `run_command`
strings — is POSIX shell: single quotes, `$VAR`, `&&`, `>>`, env-var prefixes.
On POSIX that is simply `subprocess.run(cmd, shell=True)` (/bin/sh), unchanged.
On native Windows `shell=True` would hand the string to **cmd.exe** — a
different language where `'` is not a quote and a `->` in an echo becomes a
redirect — so commands are routed through Git Bash instead: one command
language everywhere, and the engine already hard-requires git, which bundles
bash on Windows.

`C:\\Windows\\System32\\bash.exe` (the WSL launcher) is deliberately never
used: running flow commands inside WSL is exactly what native Windows support
must not silently do.

Known limitation (Windows): on timeout the bash process is killed but its
children may survive (no process groups by default) — same class of risk the
previous cmd.exe path had.
"""
from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class ShellNotFound(RuntimeError):
    pass


def _git_relative_candidates() -> list[Path]:
    """bash.exe as shipped by Git for Windows, located relative to git.exe —
    the reliable route, immune to whatever else sits on PATH."""
    git = shutil.which("git")
    if not git:
        return []
    # <root>/cmd/git.exe (typical PATH entry) or <root>/mingw64/bin/git.exe
    root = Path(git).resolve().parent.parent
    return [root / "bin" / "bash.exe",
            root / "usr" / "bin" / "bash.exe",
            root.parent / "bin" / "bash.exe"]      # when git.exe was mingw64/bin's


def _conventional_candidates() -> list[Path]:
    out = []
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Git" / "bin" / "bash.exe")
    local = os.environ.get("LocalAppData")
    if local:
        out.append(Path(local) / "Programs" / "Git" / "bin" / "bash.exe")
    return out


@functools.lru_cache(maxsize=1)
def find_bash() -> str:
    """Locate the bash used to run flow commands on Windows.

    Order: the SAAGE_SHELL env var (a bash path, or the literal ``cmd`` to
    force the legacy cmd.exe behavior) → bash.exe relative to git.exe →
    conventional Git-for-Windows install dirs → PATH, excluding the System32
    WSL launcher. No silent cmd.exe fallback: running POSIX-sh flow commands
    in the wrong dialect fails in confusing, data-dependent ways — better to
    name the fix.
    """
    override = os.environ.get("SAAGE_SHELL")
    if override:
        if _is_cmd(override) or Path(override).is_file() or shutil.which(override):
            return override
        raise ShellNotFound(
            f"SAAGE_SHELL={override!r} is not an executable (and not 'cmd')")
    for cand in (*_git_relative_candidates(), *_conventional_candidates()):
        if cand.is_file():
            return str(cand)
    which = shutil.which("bash")
    if which and "system32" not in which.lower():
        return which
    raise ShellNotFound(
        "no POSIX bash found to run flow commands on Windows — install Git "
        "for Windows (https://git-scm.com/download/win), or point SAAGE_SHELL "
        "at a bash executable (SAAGE_SHELL=cmd forces cmd.exe, for flows "
        "written in that dialect)"
    )


def _is_cmd(shell: str) -> bool:
    """The cmd.exe escape hatch — match `cmd`, `cmd.exe`, or any path to it.
    ntpath.basename handles both separator styles on every host OS."""
    import ntpath
    return ntpath.basename(shell.strip()).lower() in ("cmd", "cmd.exe")


def run_shell(command: str, *, cwd, env: dict | None = None,
              timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run one flow-command string; capture text output (UTF-8, `errors=replace`
    — odd bytes from a command must degrade to ``�``, never crash the engine).

    Note (Windows): the command travels to bash as one argv element through
    CreateProcess quoting; a ``\\`` immediately before a ``"`` inside the
    command gets doubled in transit (`subprocess.list2cmdline` rules). Windows
    paths in commands are fine quoted (`"C:\\ws\\file"`) as the bundled flows
    do; avoid a quoted segment that *ends* in a backslash.
    """
    kwargs: dict = dict(cwd=cwd, env=env, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=timeout)
    if os.name != "nt":
        return subprocess.run(command, shell=True, **kwargs)
    shell = find_bash()
    if _is_cmd(shell):
        return subprocess.run(command, shell=True, **kwargs)
    return subprocess.run([shell, "-c", command], **kwargs)
