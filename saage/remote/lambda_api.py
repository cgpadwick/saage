"""Lambda Cloud provisioning — the deferred layer from the plan (§9), v1.

`saage remote spawn` launches a GPU instance, registers it as an ssh target,
and from there the normal handoff path takes over — provisioning is strictly
additive. `saage remote terminate` stops the meter (on Lambda, only API/console
termination stops billing; an OS shutdown puts the instance in Alert status
and KEEPS billing).

Gotchas encoded here so nobody re-learns them:
- The API sits behind Cloudflare; default urllib user-agents get a 403
  (error 1010). Every request sends a real User-Agent.
- Launch is given ONLY the saage key (the automation must be able to ssh);
  other registered keys (e.g. the user's laptop key) are appended to
  authorized_keys right after boot, fetched from the account via the API.
- A launch that never reaches `active` is terminated, not leaked.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request

log = logging.getLogger("saage.remote")

BASE = "https://cloud.lambda.ai/api/v1"
UA = "saage-remote/0.1 (+https://github.com/cgpadwick/saage)"
SAAGE_KEY_NAME = "saage-remote"

# preference order per GPU class; "auto" = cheapest type with capacity
GPU_PREFS = {
    "a10": ["gpu_1x_a10"],
    "a100": ["gpu_1x_a100_sxm4", "gpu_1x_a100"],
    "h100": ["gpu_1x_h100_pcie", "gpu_1x_h100_sxm5"],
    "gh200": ["gpu_1x_gh200"],
}


class LambdaError(RuntimeError):
    pass


class LambdaAPI:
    def __init__(self, api_key: str):
        self.key = api_key.strip()

    def _request(self, path: str, payload: dict | None = None) -> dict:
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {self.key}", "User-Agent": UA,
                     "Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()[:400].decode(errors="replace")
            raise LambdaError(f"Lambda API {path} -> {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:    # network/DNS — must be catchable too
            raise LambdaError(f"Lambda API {path} unreachable: {exc.reason}") from exc

    # -- read ------------------------------------------------------------------

    def instances(self) -> list[dict]:
        return self._request("/instances")["data"]

    def instance(self, iid: str) -> dict:
        return self._request(f"/instances/{iid}")["data"]

    def instance_types(self) -> dict:
        return self._request("/instance-types")["data"]

    def ssh_keys(self) -> list[dict]:
        return self._request("/ssh-keys")["data"]

    # -- write -----------------------------------------------------------------

    def ensure_ssh_key(self, name: str, public_key: str) -> None:
        if any(k["name"] == name for k in self.ssh_keys()):
            return
        self._request("/ssh-keys", {"name": name, "public_key": public_key})

    def launch(self, instance_type: str, region: str, ssh_key_name: str,
               name: str) -> str:
        out = self._request("/instance-operations/launch", {
            "instance_type_name": instance_type,
            "region_name": region,
            "ssh_key_names": [ssh_key_name],
            "name": name,
        })
        return out["data"]["instance_ids"][0]

    def terminate(self, instance_ids: list[str]) -> list[dict]:
        out = self._request("/instance-operations/terminate",
                            {"instance_ids": instance_ids})
        return out["data"]["terminated_instances"]


def pick_instance_type(avail: dict, gpu: str = "auto") -> tuple[str, str, float]:
    """Choose (instance_type, region, $/hr) from the /instance-types payload.

    gpu: a class key from GPU_PREFS, an exact instance type name, or "auto"
    (cheapest GPU type with capacity anywhere). Raises with what WAS available
    so the error is actionable.
    """
    def regions(t: dict) -> list[str]:
        return [r["name"] for r in t.get("regions_with_capacity_available", [])]

    candidates: list[str]
    if gpu == "auto":
        candidates = sorted(
            (name for name, t in avail.items() if regions(t)),
            key=lambda n: avail[n]["instance_type"]["price_cents_per_hour"],
        )
    elif gpu in GPU_PREFS:
        candidates = GPU_PREFS[gpu]
    else:
        candidates = [gpu]

    for name in candidates:
        t = avail.get(name)
        if t and regions(t):
            return (name, regions(t)[0],
                    t["instance_type"]["price_cents_per_hour"] / 100)

    with_capacity = sorted(n for n, t in avail.items() if regions(t))
    raise LambdaError(
        f"no capacity for {gpu!r}. Types with capacity right now: "
        f"{', '.join(with_capacity) or '(none)'}"
    )


def wait_active(api: LambdaAPI, iid: str, timeout_s: int = 900,
                poll_interval: float = 15) -> dict:
    """Poll until the instance is active (it then has an IP). On timeout the
    instance is terminated — never leak a half-launched node. A transient API
    error mid-poll must NOT abort the wait (aborting here would leak a billing
    instance); only the wall-clock deadline gives up."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            inst = api.instance(iid)
        except LambdaError as exc:
            log.warning("poll for %s failed (%s) — retrying", iid, exc)
            time.sleep(poll_interval)
            continue
        status = inst["status"]
        if status == "active" and inst.get("ip"):
            return inst
        if status in ("terminated", "terminating"):
            raise LambdaError(f"instance {iid} went to {status} during boot")
        time.sleep(poll_interval)
    try:
        api.terminate([iid])
        note = "terminated it"
    except LambdaError as exc:
        note = (f"AND terminating it failed ({exc}) — instance {iid} may still "
                f"be billing; terminate it in the Lambda dashboard")
    raise LambdaError(f"instance {iid} not active after {timeout_s}s — {note}")


def wait_ssh(host: str, user: str, key_path: str, timeout_s: int = 300) -> None:
    # a reused IP may have a stale known_hosts entry from a previous instance
    subprocess.run(["ssh-keygen", "-R", host], capture_output=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        proc = subprocess.run(
            ["ssh", "-i", key_path, "-o", "IdentitiesOnly=yes",
             "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
             "-o", "ConnectTimeout=10", f"{user}@{host}", "true"],
            capture_output=True)
        if proc.returncode == 0:
            return
        time.sleep(10)
    raise LambdaError(f"instance at {host} is active but ssh never came up")
