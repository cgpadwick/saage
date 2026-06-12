"""Thunder Compute backend — wraps the `tnr` CLI (plan §9's deferred half).

Differences from Lambda that shape this module:
- auth lives in the CLI (`tnr login --token …`), not per-request;
- `--json` output is preceded by a human status line ("Fetching …"), so
  parsing must start at the first JSON character;
- every instance gets its own ssh keypair, returned ONCE by `tnr create`
  — the caller must persist it;
- connection is plain ssh, but on a non-standard per-instance port.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("saage.remote")


class ThunderError(RuntimeError):
    pass


@dataclass
class ThunderInstance:
    id: str                  # numeric — what `tnr delete` accepts
    uuid: str
    ip: str
    port: int
    status: str


def _tnr_bin() -> str:
    found = shutil.which("tnr") or str(Path.home() / ".local" / "bin" / "tnr")
    if not Path(found).exists():
        raise ThunderError(
            "tnr CLI not found — install from "
            "https://github.com/Thunder-Compute/thunder-cli/releases and "
            "`tnr login --token <api_token>`")
    return found


def parse_tnr_json(output: str):
    """tnr --json prints a human line before the JSON — start at the
    first '{' or '['."""
    starts = [i for i in (output.find("{"), output.find("[")) if i != -1]
    if not starts:
        raise ThunderError(f"no JSON in tnr output: {output[:200]!r}")
    return json.loads(output[min(starts):])


def _run(args: list[str], *, timeout: int = 300) -> str:
    proc = subprocess.run([_tnr_bin(), *args, "--json", "-y"],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise ThunderError(f"tnr {' '.join(args)} failed: "
                           f"{(proc.stderr or proc.stdout)[-500:]}")
    return proc.stdout


def instances() -> list[ThunderInstance]:
    out = parse_tnr_json(_run(["status"]))
    return [ThunderInstance(id=str(i.get("id", "")), uuid=i["uuid"],
                            ip=i.get("ip", ""),
                            port=int(i.get("port") or 22),
                            status=i.get("status", "?")) for i in out]


def create(*, gpu: str = "a6000", vcpus: int = 4, disk_gb: int = 100,
           template: str = "base") -> tuple[str, str]:
    """Launch one prototyping instance; returns (uuid, private_key_pem).
    Billing starts here — callers must terminate on any later failure.
    (Deletion needs the numeric id from `instances()`, not the uuid.)"""
    out = parse_tnr_json(_run([
        "create", "--gpu", gpu, "--mode", "prototyping",
        "--template", template, "--num-gpus", "1",
        "--vcpus", str(vcpus), "--primary-disk", str(disk_gb)]))
    if "error" in out:
        raise ThunderError(f"tnr create: {out['error']}")
    return out["uuid"], out["key"]


def delete_by_uuid(uuid: str) -> None:
    for inst in instances():
        if inst.uuid == uuid:
            _run(["delete", inst.id])
            return
    raise ThunderError(f"no instance matching {uuid!r}")


def delete_by_addr(ip: str, port: int) -> bool:
    """Thunder instances share proxy IPs — (ip, port) is the unique address.
    Matching by IP alone could delete a sibling instance."""
    for inst in instances():
        if inst.ip == ip and inst.port == port:
            _run(["delete", inst.id])
            return True
    return False


def wait_running(uuid: str, *, timeout: int = 600) -> ThunderInstance:
    """Poll until RUNNING; on timeout the instance is deleted, never leaked."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for inst in instances():
            if inst.uuid == uuid and inst.status == "RUNNING" and inst.ip:
                return inst
        time.sleep(10)
    try:
        delete_by_uuid(uuid)
        raise ThunderError(f"instance {uuid} not RUNNING after {timeout}s — deleted")
    except ThunderError:
        raise
    except Exception as exc:
        raise ThunderError(
            f"instance {uuid} not RUNNING after {timeout}s AND delete failed "
            f"({exc}) — remove it in the Thunder console") from exc
