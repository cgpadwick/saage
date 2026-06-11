"""SshTarget — v1's only backend: any SSH-able host.

There is no provisioning: the user brings a running node (a LAN box, a
hand-launched cloud instance, localhost for tests). `stop` stops the RUN,
never the box. A provisioning backend later produces a host+user and hands it
to this exact path (plan §9).
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from .creds import Target
from .sshio import SSHConn, SSHError


class PreflightError(RuntimeError):
    pass


@dataclass
class SshTarget:
    target: Target

    @property
    def conn(self) -> SSHConn:
        return SSHConn(host=self.target.host, user=self.target.user,
                       key=self.target.key, port=self.target.port)

    @staticmethod
    def run_dir(run_id: str) -> str:
        # relative to the node $HOME (ssh + rsync both resolve it there)
        return f".saage_runs/{run_id}"

    # -- lifecycle -------------------------------------------------------------

    def preflight(self, *, need_gpu: bool = False) -> list[str]:
        """Verify the target instead of provisioning one. Returns warnings."""
        warnings: list[str] = []
        conn = self.conn
        try:
            conn.run("true", timeout=30)
        except SSHError as exc:
            raise PreflightError(
                f"cannot ssh to target {self.target.name!r} ({conn.dest}): {exc}"
            ) from exc
        # node-side rsync only matters when the local side will use rsync;
        # in tar mode the node needs tar instead
        from .sshio import _use_rsync
        for tool in ("tmux", "git", "rsync" if _use_rsync() else "tar"):
            if not conn.ok(f"command -v {tool} >/dev/null"):
                # lean cloud images (e.g. Thunder's base template) ship without
                # tmux — tell the user the one-liner instead of just refusing
                raise PreflightError(
                    f"target {self.target.name!r} is missing {tool!r} — install "
                    f"it: ssh the box and run `sudo apt-get install -y {tool}`")
        busy = self.sessions()
        if busy:
            raise PreflightError(
                f"target {self.target.name!r} already has a saage run: {', '.join(busy)} "
                f"(one run per box; `saage remote kill` it first)"
            )
        if not conn.ok("command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null"):
            msg = f"target {self.target.name!r} has no working GPU (nvidia-smi)"
            if need_gpu:
                raise PreflightError(msg)
            warnings.append(msg + " — continuing (CPU mode)")
        return warnings

    def start(self, run_id: str) -> None:
        session = shlex.quote(f"saage-{run_id}")
        self.conn.run(
            f"tmux new-session -d -s {session} "
            f"{shlex.quote(f'bash $HOME/{self.run_dir(run_id)}/start.sh')}"
        )

    def stop(self, run_id: str) -> None:
        """Run the per-run stop script: kill session, final collect, drop secrets."""
        self.conn.run(f"bash $HOME/{self.run_dir(run_id)}/stop.sh", timeout=60)

    # -- introspection -----------------------------------------------------------

    def sessions(self) -> list[str]:
        proc = self.conn.run("tmux ls 2>/dev/null", check=False)
        names = [line.split(":")[0] for line in proc.stdout.splitlines() if ":" in line]
        return [n for n in names if n.startswith("saage-")]

    def session_alive(self, run_id: str) -> bool:
        return self.conn.ok(f"tmux has-session -t {shlex.quote(f'saage-{run_id}')} 2>/dev/null")

    def read_status(self, run_id: str) -> dict:
        import json
        proc = self.conn.run(f"cat $HOME/{self.run_dir(run_id)}/status.json", check=False)
        if proc.returncode != 0:
            return {}
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {}

    def tail_log(self, run_id: str, lines: int = 25) -> str:
        proc = self.conn.run(
            f"tail -n {int(lines)} $HOME/{self.run_dir(run_id)}/saage.log", check=False)
        return proc.stdout
