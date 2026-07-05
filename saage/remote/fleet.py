"""Coordinator-on-a-box: `saage remote sweep-up` / `sweep-watch` / `sweep-down`.

True fire-and-forget for batched flows (docs/batched_hillclimb_plan.md §5
revisited): instead of the laptop coordinating rounds, the *coordinator
flow itself* is handed off to one spawned box, which dispatches the batch
rounds to its sibling worker boxes (Lambda or Thunder Compute). The
laptop's jobs shrink to sweep-up, and later sweep-watch/sweep-down.

Security model — the coordinator box must never hold keys it doesn't need:

- a **per-sweep ssh keypair** is generated on the laptop and authorized
  ONLY on this sweep's worker boxes; the user's main saage key (which can
  reach every registered target) never leaves the laptop.
- the coordinator gets a **scoped saage_home** inside its run dir (pushed
  0600): a credentials.toml listing only the sweep's workers + the
  [storage] mirror section. `SAAGE_HOME` in the run env points there.
- the cloud API keys stay on the laptop — the coordinator cannot spawn
  or terminate anything. Teardown is sweep-down/sweep-watch, laptop-side.

Idle-fleet control (the "C" in the plan): the coordinator flow drops a
marker artifact when the batch rounds end; `sweep-watch` polls the R2
mirror and tears the workers down at the marker (the coordinator keeps
running its final-train/submission tail alone), then everything at the
run's final phase — after pulling the artifacts from the mirror.

Everything a sweep creates is named `sweep-<id>-…` so teardown can never
touch an unrelated box.
"""
from __future__ import annotations

import logging
import secrets as pysecrets
import subprocess
import time
from pathlib import Path

from ..paths import saage_home
from . import thunder_api
from .creds import (CredsError, Target, add_target, list_targets, load_creds,
                    storage_config)
from .handoff import _gen_run_id, handoff
from .lambda_api import (LambdaAPI, SAAGE_KEY_NAME, pick_instance_type,
                         wait_active, wait_ssh)
from .observe import _fetch_from_bucket, _status_from_bucket, bucket_names
from .provision import provision_node
from .sshio import SSHConn
from .state import RunState, find_run

log = logging.getLogger("saage.remote")

KEY_RELPATH = "saage_home/ssh/sweep_key"          # inside the coordinator run dir
BATCH_DONE_MARKER = "batch_done.marker"           # flow drops it after the rounds
_FINAL = {"done", "failed", "timeout", "killed"}


class FleetError(RuntimeError):
    pass


def sweep_names(sweep_id: str, n_workers: int) -> tuple[str, list[str]]:
    return (f"sweep-{sweep_id}-c",
            [f"sweep-{sweep_id}-w{i + 1}" for i in range(n_workers)])


def scoped_credentials(workers: list, key_path: str,
                       storage=None) -> str:
    """The coordinator's credentials.toml: this sweep's workers and the
    storage mirror — nothing else. Pure (unit-tested)."""
    lines = []
    if storage:
        lines += ["[storage]",
                  f'endpoint = "{storage.endpoint}"',
                  f'bucket = "{storage.bucket}"',
                  f'access_key = "{storage.access_key}"',
                  f'secret_key = "{storage.secret_key}"',
                  f'region = "{storage.region}"', ""]
    for w in workers:
        lines += [f"[targets.{w.name}]",
                  f'host = "{w.host}"']
        if w.user:
            lines.append(f'user = "{w.user}"')
        if w.port != 22:
            lines.append(f"port = {w.port}")
        if w.hourly_usd:
            lines.append(f"hourly_usd = {w.hourly_usd}")
        if w.max_runs != 1:
            lines.append(f"max_runs = {w.max_runs}")
        lines += [f"key = '{key_path}'", ""]
    return "\n".join(lines) + "\n"


def _gen_sweep_key(sweep_id: str) -> Path:
    key = saage_home() / "ssh" / f"sweep_{sweep_id}"
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key),
                        "-C", f"saage-sweep-{sweep_id}", "-q"], check=True)
    return key


# --------------------------------------------------------------------------- #
# cloud backends: spawn one named box -> registered Target; terminate by host
# --------------------------------------------------------------------------- #

class LambdaBackend:
    name = "lambda"

    def __init__(self):
        key = (load_creds().get("lambda") or {}).get("api_key")
        if not key:
            raise CredsError("no [lambda] api_key in credentials.toml")
        self.api = LambdaAPI(key)
        self._spawned: list[str] = []

    def spawn(self, name: str, gpu: str, slots: int) -> Target:
        main_key = saage_home() / "ssh" / "saage_ed25519"
        self.api.ensure_ssh_key(SAAGE_KEY_NAME,
                                main_key.with_suffix(".pub").read_text().strip())
        itype, region, price = pick_instance_type(self.api.instance_types(), gpu)
        log.info("launching %s in %s ($%.2f/hr) as %r", itype, region, price, name)
        iid = self.api.launch(itype, region, SAAGE_KEY_NAME, f"saage-{name}")
        self._spawned.append(iid)
        inst = wait_active(self.api, iid)
        wait_ssh(inst["ip"], "ubuntu", str(main_key))
        add_target(name, inst["ip"], user="ubuntu", hourly_usd=price,
                   max_runs=slots)
        return list_targets()[name]

    def terminate_target(self, target: Target) -> bool:
        ids = [i["id"] for i in self.api.instances()
               if i.get("ip") == target.host]
        if ids:
            self.api.terminate(ids)
        return bool(ids)

    def emergency_cleanup(self) -> None:
        for iid in self._spawned:
            try:
                self.api.terminate([iid])
            except Exception as exc:
                log.error("terminate %s failed (%s) — it may still be billing",
                          iid, exc)


class ThunderBackend:
    """Thunder Compute (REST API — saage.remote.thunder_api). Cheap
    prototyping boxes (~$0.35/hr a6000); per-instance ssh keys returned once
    by create; ssh NAT'd on a per-instance port; tmux installed post-boot."""
    name = "thunder"

    def __init__(self):
        token = (load_creds().get("thundercompute") or {}).get("api_token")
        if not token:
            raise CredsError("no [thundercompute] api_token in credentials.toml")
        self.api = thunder_api.ThunderAPI(token)
        self._spawned: list[str] = []   # instance ids

    def spawn(self, name: str, gpu: str, slots: int,
              avoid_ips: tuple = ()) -> Target:
        gpu_type, price = thunder_api.pick_gpu(self.api, gpu)
        log.info("launching thunder %s ($%.2f/hr) as %r", gpu_type, price, name)
        inst = None
        for attempt in range(3):
            iid, key_pem = self.api.create(gpu_type)
            self._spawned.append(iid)
            inst = thunder_api.wait_running(self.api, iid, timeout_s=1200)
            if inst.get("ip") not in avoid_ips:
                break
            # Thunder proxies block hairpin connections: a box cannot reach
            # a sibling behind its own shared proxy IP, so a worker on the
            # coordinator's IP would be quarantined every round
            log.warning("%s landed on an avoided proxy IP (%s) — respawning",
                        name, inst.get("ip"))
            self.api.delete(iid)
            self._spawned.remove(iid)
            inst, iid = None, None
        if inst is None:
            raise FleetError(
                f"could not get {name!r} off the avoided proxy IPs in 3 tries")
        ip, port = inst["ip"], int(inst.get("port") or 22)
        key_path = saage_home() / "ssh" / f"{name}_key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(key_pem)
        key_path.chmod(0o600)
        conn = SSHConn(host=ip, user="ubuntu", key=key_path, port=port)
        _wait_conn(conn)
        # lean image: preflight needs tmux
        conn.run("command -v tmux >/dev/null || (sudo apt-get update -qq && "
                 "sudo apt-get install -y -qq tmux)", timeout=300)
        add_target(name, ip, user="ubuntu", port=port, hourly_usd=price,
                   key=str(key_path), max_runs=slots)
        return list_targets()[name]

    def terminate_target(self, target: Target) -> bool:
        # Thunder instances share proxy IPs — match on (ip, port), never
        # IP alone, or teardown could delete a sibling sweep box
        insts = self.api.instances(update_ips=True)
        hits = [iid for iid, m in insts.items()
                if m.get("ip") == target.host
                and int(m.get("port") or 22) == target.port]
        for iid in hits:
            self.api.delete(iid)
        return bool(hits)

    def emergency_cleanup(self) -> None:
        for iid in self._spawned:
            try:
                self.api.delete(iid)
            except Exception as exc:
                log.error("thunder delete %s failed (%s) — check the console",
                          iid, exc)


def _wait_conn(conn: SSHConn, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if conn.ok("true"):
            return
        time.sleep(5)
    raise FleetError(f"ssh to {conn.dest} not ready after {timeout}s")


def backend_for(cloud: str):
    if cloud == "lambda":
        return LambdaBackend()
    if cloud == "thunder":
        return ThunderBackend()
    raise FleetError(f"unknown cloud {cloud!r} (lambda|thunder)")


# --------------------------------------------------------------------------- #
# sweep-up / sweep-down / sweep-watch
# --------------------------------------------------------------------------- #

def sweep_up(flow: str, *, n_workers: int, cloud: str = "thunder",
             gpu: str = "auto",
             set_args: dict | None = None, extra_env: dict | None = None,
             worker_slots: int = 1,
             provision_cmd: str | None = None,
             provision_files: Path | None = None,
             batch_targets_key: str = "batch_targets",
             max_run_days: float = 2.0, sync_interval: int = 60,
             bootstrap_timeout: int = 1800,
             workspace_mode: str = "auto", dirty: str = "abort") -> RunState:
    """Spawn 1 coordinator + N workers, scope credentials, hand off the flow.

    On ANY failure after the first launch, every box this call spawned is
    terminated — a failed sweep-up never leaks a billing node.
    """
    sweep_id = pysecrets.token_hex(2)
    coord_name, worker_names = sweep_names(sweep_id, n_workers)
    backend = backend_for(cloud)
    sweep_key = _gen_sweep_key(sweep_id)
    pub = sweep_key.with_suffix(".pub").read_text().strip()

    try:
        coordinator = backend.spawn(coord_name, gpu, 1)
        avoid = ({"avoid_ips": (coordinator.host,)}
                 if isinstance(backend, ThunderBackend) else {})
        workers = [backend.spawn(n, gpu, worker_slots, **avoid)
                   for n in worker_names]

        # the per-sweep key is the ONLY laptop credential the coordinator
        # gets, and it opens only these boxes
        for w in workers:
            SSHConn(host=w.host, user=w.user, key=w.key, port=w.port).run(
                "cat >> ~/.ssh/authorized_keys", input=pub + "\n")

        # coordinator-side data (e.g. the prepared competition split)
        if provision_cmd:
            provision_node(coordinator, provision_cmd, files=provision_files)

        run_id = _gen_run_id(Path(flow).resolve().parent.name)
        home = SSHConn(host=coordinator.host, user=coordinator.user,
                       key=coordinator.key, port=coordinator.port
                       ).run("echo $HOME").stdout.strip()
        rdir_abs = f"{home}/.saage_runs/{run_id}"
        creds_text = scoped_credentials(workers, f"{rdir_abs}/{KEY_RELPATH}",
                                        storage=storage_config())

        rs = handoff(
            flow=flow, target=coordinator,
            set_args={batch_targets_key: ",".join(w.name for w in workers),
                      **(set_args or {})},
            extra_env={"SAAGE_HOME": f"{rdir_abs}/saage_home",
                       **(extra_env or {})},
            push_files={"saage_home/credentials.toml": creds_text,
                        KEY_RELPATH: sweep_key.read_text()},
            run_id=run_id, need_gpu=True, max_run_days=max_run_days,
            sync_interval=sync_interval, bootstrap_timeout=bootstrap_timeout,
            workspace_mode=workspace_mode, dirty=dirty,
        )
        rs.update(sweep_id=sweep_id, sweep_cloud=cloud,
                  sweep_boxes=[coord_name, *worker_names])
        rs.event("sweep_up", sweep_id=sweep_id, cloud=cloud,
                 workers=worker_names)
        return rs
    except BaseException:
        backend.emergency_cleanup()
        raise


def sweep_targets(sweep_id: str, targets: dict | None = None) -> dict:
    """Only this sweep's boxes — teardown can never touch anything else."""
    targets = list_targets() if targets is None else targets
    prefix = f"sweep-{sweep_id}-"
    return {n: t for n, t in targets.items() if n.startswith(prefix)}


def sweep_down(sweep_id: str, *, only_workers: bool = False,
               clouds: tuple[str, ...] = ("thunder", "lambda")) -> list[str]:
    """Terminate this sweep's boxes (all, or workers only) and de-register
    them. Artifacts survive on the R2 mirror."""
    mine = sweep_targets(sweep_id)
    if only_workers:
        mine = {n: t for n, t in mine.items()
                if n.rsplit("-", 1)[-1].startswith("w")}
    if not mine:
        return []
    backends = []
    for c in clouds:
        try:
            backends.append(backend_for(c))
        except Exception:                 # that cloud isn't configured — fine
            pass
    done = []
    for name, target in mine.items():
        if any(b.terminate_target(target) for b in backends):
            done.append(name)
            log.info("terminated %s (%s)", name, target.host)
        else:
            log.warning("%s (%s): no live instance found — already gone",
                        name, target.host)
    _remove_targets(list(mine))
    return done


def sweep_watch(run_ref: str | None = None, *, interval: int = 60,
                fetch_dest: Path | None = None, clock=time) -> dict:
    """Babysit a handed-off sweep from the laptop, via the R2 mirror only:

    - when the flow's BATCH_DONE marker appears in the run's mirror,
      terminate the *workers* (the coordinator still runs its tail);
    - when the coordinator's phase goes final, fetch the artifacts from
      the mirror and terminate everything.

    Returns a summary dict. Run it detached (`nohup … &`) for true
    fire-and-forget.
    """
    rs = find_run(run_ref)
    state = rs.state()
    sweep_id = state.get("sweep_id")
    if not sweep_id:
        raise FleetError(f"run {rs.run_id} was not started by sweep-up")
    storage = storage_config()
    if not storage:
        raise FleetError("sweep-watch needs the [storage] mirror configured")

    workers_down = False
    while True:
        names = bucket_names(storage, rs.run_id)
        status = _status_from_bucket(storage, rs.run_id)
        phase = status.get("phase")
        if not workers_down and BATCH_DONE_MARKER in names:
            log.info("batch rounds done — releasing the workers")
            sweep_down(sweep_id, only_workers=True)
            workers_down = True
        if phase in _FINAL:
            dest = Path(fetch_dest) if fetch_dest else \
                Path.cwd() / "results" / rs.run_id
            dest.mkdir(parents=True, exist_ok=True)
            got = _fetch_from_bucket(storage, rs.run_id, dest)
            rs.update(phase=phase)
            rs.event("sweep_watch_final", phase=phase, fetched=len(got))
            downed = sweep_down(sweep_id)
            return {"phase": phase, "fetched": got, "dest": str(dest),
                    "terminated": downed, "workers_released_early": workers_down}
        clock.sleep(interval)


def _remove_targets(names: list[str]) -> None:
    import re
    path = saage_home() / "credentials.toml"
    text = path.read_text()
    for name in names:
        text = re.sub(rf"\n*\[targets\.{re.escape(name)}\]\n(?:[^\[\n][^\n]*\n)*",
                      "\n", text)
    path.write_text(text)
    path.chmod(0o600)
