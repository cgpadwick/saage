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

On timeout the whole process GROUP dies, not just the shell: a flow command is
almost always compound (`python train.py && python read_score.py`), so the
direct child is /bin/sh and the real workload is a grandchild. `subprocess.run`'s
own timeout kills only the direct child, orphaning the workload — it keeps the
GPU, keeps writing checkpoints under later steps, and on Windows keeps the
output pipes open so the engine blocks forever in communicate(). Instead the
timeout path launches the command as a session leader and kills the process
group (POSIX) / runs `taskkill /F /T` (Windows). A workload that re-sessions
itself (setsid/daemonize) can escape — see _kill_tree.
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

    With ``timeout`` set, expiry raises `subprocess.TimeoutExpired` (partial
    output attached, mirroring `subprocess.run`) after killing the whole
    process group — see the module docstring for why the group and not just
    the shell.

    Note (Windows): the command travels to bash as one argv element through
    CreateProcess quoting; a ``\\`` immediately before a ``"`` inside the
    command gets doubled in transit (`subprocess.list2cmdline` rules). Windows
    paths in commands are fine quoted (`"C:\\ws\\file"`) as the bundled flows
    do; avoid a quoted segment that *ends* in a backslash.
    """
    if os.name != "nt":
        argv, use_shell = command, True
    else:
        shell = find_bash()
        if _is_cmd(shell):
            argv, use_shell = command, True
        else:
            argv, use_shell = [shell, "-c", command], False
    if timeout is None:                      # untimed path: unchanged behavior
        return subprocess.run(argv, shell=use_shell, cwd=cwd, env=env,
                              capture_output=True, **_CAPTURE)
    return _run_tree_killable(argv, use_shell, command, timeout, cwd=cwd, env=env)


# one output contract for both run paths: odd bytes degrade to �, never crash
_CAPTURE: dict = dict(text=True, encoding="utf-8", errors="replace")


def _kill_tree(p: subprocess.Popen) -> None:
    """Best-effort kill of the process and every descendant.

    POSIX kills the process group; Windows walks the tree with taskkill. A
    workload that re-sessions itself (setsid, daemonization, nohup
    double-fork) escapes the group and can survive — that residue is why the
    engine reports "process group killed", not "tree" (an unconditionally
    true claim would hide survivors from whoever debugs the run).
    """
    if os.name != "nt":
        import signal
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()                         # group already gone; reap the root
    else:
        _kill_tree_windows(p.pid)
        p.kill()


def _kill_tree_windows(pid: int) -> None:
    # taskkill /T walks the parent→child tree; /F force-kills. Killing the
    # grandchildren is also what releases the inherited stdout/stderr pipe
    # handles, so the communicate() that follows can return.
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True)


def _run_tree_killable(argv, use_shell: bool, command: str, timeout: float,
                       *, cwd, env) -> subprocess.CompletedProcess:
    """The timed variant of run_shell: launch so the whole group can be killed.

    POSIX: `start_new_session=True` makes the shell a session (and process
    group) leader; its children inherit the group, so one killpg reaps the
    compound command's real workload as well as the shell. The group is
    ALSO killed when communicate() raises anything else — Ctrl-C
    (KeyboardInterrupt) must not orphan a training job that, being in its own
    session, never saw the terminal's SIGINT. Residual risk, documented: a
    SIGKILL of the engine itself (or terminal SIGHUP reaching only the
    engine) runs no handler, and a workload that re-sessions itself escapes
    the group kill.
    """
    popen: dict = dict(cwd=cwd, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, shell=use_shell, **_CAPTURE)
    if os.name != "nt":
        popen["start_new_session"] = True
    with subprocess.Popen(argv, **popen) as p:   # ctx mgr: pipes always closed
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            try:                             # reap; pipes closed by the kill
                out, err = p.communicate(timeout=10)
            except subprocess.TimeoutExpired as e2:  # a pipe-holder survived —
                p.kill()                     # salvage what communicate buffered
                out = e2.output if isinstance(e2.output, str) else ""
                err = e2.stderr if isinstance(e2.stderr, str) else ""
            raise subprocess.TimeoutExpired(command, timeout,
                                            output=out, stderr=err)
        except BaseException:                # Ctrl-C / MemoryError / anything:
            _kill_tree(p)                    # never leave the group running
            raise
    return subprocess.CompletedProcess(command, p.returncode, out, err)
