"""Thin subprocess wrappers around ssh (+ rsync, with a portable fallback).

Every byte the remote machinery moves to or from a node goes through SSHConn,
so the key, options, and timeouts live in exactly one place. BatchMode keeps
everything non-interactive: a target that would prompt for a password fails
fast instead of hanging a handoff.

Windows notes (the local machine may be native Windows):
- stdin is always BINARY: text-mode subprocess stdin would translate \\n to
  \\r\\n, corrupting every bash script and run_env pushed to a Linux node.
- rsync is not bundled with Git for Windows, so when rsync is missing the
  transfer methods fall back to tar-over-ssh: directories are packed with
  Python's tarfile (exclude semantics owned here, not by a host tar) and
  streamed over stdin; fetches stream `tar -czf -` back. Force the fallback
  with SAAGE_FORCE_TAR=1 (used by tests on rsync-equipped hosts).
"""
from __future__ import annotations

import fnmatch
import io
import os
import shlex
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path


class SSHError(RuntimeError):
    """An ssh/rsync invocation failed."""


def _use_rsync() -> bool:
    return bool(shutil.which("rsync")) and not os.environ.get("SAAGE_FORCE_TAR")


def _excluded(rel_posix: str, excludes: tuple[str, ...]) -> bool:
    """rsync-style component matching: a pattern excludes a path when any
    path component matches it (so ".git" prunes the tree, "*.log" any log)."""
    return any(fnmatch.fnmatch(part, pat)
               for part in rel_posix.split("/") for pat in excludes)


def _pack_dir(src: Path, excludes: tuple[str, ...]) -> bytes:
    """A gzipped tar of `src`'s contents (arcnames POSIX-relative), built in
    memory — the payloads here are engine source and flow dirs, not datasets."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src).as_posix()
            if _excluded(rel, excludes):
                continue
            tf.add(p, arcname=rel, recursive=False)
    return buf.getvalue()


@dataclass
class SSHConn:
    host: str
    user: str | None = None
    key: Path | None = None
    port: int = 22
    connect_timeout: int = 10

    @property
    def dest(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def _opts(self) -> list[str]:
        opts = [
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-o", "ServerAliveInterval=30",
            "-p", str(self.port),
        ]
        if self.key:
            opts += ["-i", str(self.key), "-o", "IdentitiesOnly=yes"]
        return opts

    def run(self, command: str, *, input: str | bytes | None = None,
            timeout: int = 120, check: bool = True,
            binary: bool = False) -> subprocess.CompletedProcess:
        """Run `command` on the node through the login shell.

        stdin travels as bytes (str input is utf-8 encoded as-is — no newline
        translation, ever). stdout/stderr are utf-8 text unless `binary=True`,
        which returns raw bytes stdout (tar streams).
        """
        argv = ["ssh", *self._opts(), self.dest, command]
        data = input.encode() if isinstance(input, str) else input
        try:
            proc = subprocess.run(argv, input=data, capture_output=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SSHError(f"ssh {self.dest} timed out after {timeout}s: {command}") from exc
        stdout = proc.stdout if binary else proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
        proc = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        if check and proc.returncode != 0:
            out = stderr or ("<binary>" if binary else stdout)
            raise SSHError(
                f"ssh {self.dest} exited {proc.returncode}: {command}\n{out.strip()}"
            )
        return proc

    def capture(self, command: str, *, timeout: int = 120) -> str:
        return self.run(command, timeout=timeout).stdout

    def ok(self, command: str, *, timeout: int = 60) -> bool:
        try:
            return self.run(command, timeout=timeout, check=False).returncode == 0
        except SSHError:
            return False

    def write_file(self, remote_path: str, content: str, *, mode: str = "600",
                   timeout: int = 60) -> None:
        """Write `content` to the node over stdin — never via argv (ps-visible)."""
        quoted = shlex.quote(remote_path)
        self.run(
            f"install -m {mode} /dev/null {quoted} && cat > {quoted}",
            input=content, timeout=timeout,
        )

    # -- transfer: rsync when available, tar-over-ssh otherwise ----------------

    def _rsh(self) -> str:
        return shlex.join(["ssh", *self._opts()])

    def rsync_to(self, src: "Path | str", remote_dest: str, *,
                 excludes: tuple[str, ...] = (), delete: bool = False,
                 timeout: int = 900) -> None:
        """Copy a local file, or a directory's contents, to `remote_dest`
        (relative to the node $HOME)."""
        if _use_rsync():
            argv = ["rsync", "-az", *(f"--exclude={e}" for e in excludes)]
            if delete:
                argv.append("--delete")
            argv += ["-e", self._rsh(), str(src), f"{self.dest}:{remote_dest}"]
            self._rsync(argv, timeout)
            return
        # tar fallback. Callers follow rsync's trailing-slash convention for
        # dirs ("src/" = contents); tar dir mode always ships contents, which
        # is what every call site wants.
        path = Path(str(src).rstrip("/").rstrip("\\"))
        dest_q = shlex.quote(remote_dest.rstrip("/"))
        if path.is_file():
            self.run(f"cat > {dest_q}", input=path.read_bytes(), timeout=timeout)
            return
        wipe = f"rm -rf {dest_q} && " if delete else ""
        self.run(f"{wipe}mkdir -p {dest_q} && tar -xzf - -C {dest_q}",
                 input=_pack_dir(path, excludes), timeout=timeout)

    def rsync_from(self, remote_src: str, dest: Path, *, timeout: int = 900) -> None:
        """Copy a remote file, or a remote directory's contents, into local
        dir `dest`."""
        if _use_rsync():
            argv = ["rsync", "-az", "-e", self._rsh(),
                    f"{self.dest}:{remote_src}", str(dest)]
            self._rsync(argv, timeout)
            return
        src_q = shlex.quote(remote_src.rstrip("/"))
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        if self.ok(f"test -d {src_q}"):
            proc = self.run(f"tar -czf - -C {src_q} .", timeout=timeout, binary=True)
            with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:gz") as tf:
                tf.extractall(dest, filter="data")
        else:
            proc = self.run(f"cat {src_q}", timeout=timeout, binary=True)
            (dest / Path(remote_src.rstrip("/")).name).write_bytes(proc.stdout)

    @staticmethod
    def _rsync(argv: list[str], timeout: int) -> None:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SSHError(f"rsync timed out after {timeout}s") from exc
        if proc.returncode != 0:
            raise SSHError(f"rsync exited {proc.returncode}:\n{proc.stderr.strip()}")
