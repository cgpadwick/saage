"""Thunder Compute provisioning — same shape as lambda_api, different cloud.

`saage remote spawn --provider thunder` creates an instance, registers it as
an ssh target, and the normal handoff path takes over. Thunder's a6000 at
~$0.35/hr is the cheapest box saage can rent, so it's the default sweep
workhorse; Lambda stays the default provider for capacity/perf.

API facts verified against the tnr CLI source (the OpenAPI spec is
underspecified) and live read-only probes:
- Base https://api.thundercompute.com:8443, Bearer auth, and — like Lambda —
  Cloudflare 403s (error 1010) any default urllib user-agent, so every
  request sends a real User-Agent.
- POST /instances/create {cpu_cores, gpu_type, template, num_gpus,
  disk_size_gb, mode} -> {uuid, key, identifier}: `key` is a PRIVATE ssh key
  generated for the instance and returned ONLY here — we persist it under
  ~/.saage/ssh/ and register the target with it. `identifier` is the
  instance id for every later call.
- GET /instances/list -> {id: {status, ip, …}}; status "RUNNING" + a
  non-empty ip means ssh-able (user `ubuntu`). `?update_ips=true` forces an
  IP refresh.
- POST /instances/{id}/delete stops billing.
- Prototyping mode supports exactly 1 GPU; production mode exists only for
  a100xl/h100. We default to prototyping (the cheap tier).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("saage.remote")

BASE = "https://api.thundercompute.com:8443"
UA = "saage-remote/0.1 (+https://github.com/cgpadwick/saage)"

# gpu_type values accepted by /instances/create (see /v2/pricing keys)
GPU_TYPES = ("a6000", "l40", "a100xl", "h100")


class ThunderError(RuntimeError):
    pass


class ThunderAPI:
    def __init__(self, api_token: str):
        self.token = api_token.strip()

    def _request(self, path: str, payload: dict | None = None) -> dict:
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": UA,
                     "Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()[:400].decode(errors="replace")
            raise ThunderError(f"Thunder API {path} -> {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ThunderError(f"Thunder API {path} unreachable: {exc.reason}") from exc

    # -- read ------------------------------------------------------------------

    def instances(self, update_ips: bool = False) -> dict[str, dict]:
        """{instance_id: {status, ip, gpu_type, …}} — empty dict when none."""
        path = "/instances/list" + ("?update_ips=true" if update_ips else "")
        return self._request(path) or {}

    def pricing(self) -> dict[str, float]:
        return self._request("/v2/pricing").get("pricing", {})

    def availability(self) -> dict[str, dict]:
        """{gpu_type: {"1": "available"|"unavailable", …}} per GPU count."""
        return self._request("/v2/status").get("gpu_type", {})

    # -- write -----------------------------------------------------------------

    def create(self, gpu_type: str, *, cpu_cores: int = 8, template: str = "base",
               num_gpus: int = 1, disk_size_gb: int = 100,
               mode: str = "prototyping") -> tuple[str, str]:
        """Create an instance; returns (instance_id, private_key_pem).
        The private key is returned ONLY by this call — persist it."""
        out = self._request("/instances/create", {
            "cpu_cores": cpu_cores, "gpu_type": gpu_type, "template": template,
            "num_gpus": num_gpus, "disk_size_gb": disk_size_gb, "mode": mode,
        })
        return out["identifier"], out["key"]

    def delete(self, instance_id: str) -> None:
        self._request(f"/instances/{instance_id}/delete", {})


def pick_gpu(api: ThunderAPI, gpu: str = "auto") -> tuple[str, float]:
    """Choose (gpu_type, $/hr). gpu: a type from GPU_TYPES or "auto"
    (cheapest single-GPU type with capacity). Raises with what WAS available."""
    avail = api.availability()
    prices = api.pricing()

    def price(t: str) -> float:
        return prices.get(f"{t}_x1", prices.get(t, 9e9))

    def has_capacity(t: str) -> bool:
        return avail.get(t, {}).get("1") == "available"

    if gpu == "auto":
        candidates = sorted((t for t in avail if has_capacity(t)), key=price)
    else:
        candidates = [gpu]
    for t in candidates:
        if has_capacity(t):
            return t, price(t)
    up = sorted(t for t in avail if has_capacity(t))
    raise ThunderError(f"no capacity for {gpu!r}. Types with capacity right "
                       f"now: {', '.join(up) or '(none)'}")


def wait_running(api: ThunderAPI, iid: str, timeout_s: int = 900,
                 poll_interval: float = 10) -> dict:
    """Poll until RUNNING with an IP. On timeout the instance is deleted —
    never leak a billing node. Transient API errors mid-poll never abort the
    wait (aborting would leak the instance); only the deadline gives up."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            inst = api.instances(update_ips=True).get(iid) or {}
        except ThunderError as exc:
            log.warning("poll for %s failed (%s) — retrying", iid, exc)
            time.sleep(poll_interval)
            continue
        if inst.get("status") == "RUNNING" and inst.get("ip"):
            return inst
        if inst.get("status") in ("DELETED", "DELETING", "FAILED"):
            raise ThunderError(f"instance {iid} went to {inst['status']} during boot")
        time.sleep(poll_interval)
    try:
        api.delete(iid)
        note = "deleted it"
    except ThunderError as exc:
        note = (f"AND deleting it failed ({exc}) — instance {iid} may still be "
                f"billing; delete it in the Thunder console")
    raise ThunderError(f"instance {iid} not RUNNING after {timeout_s}s — {note}")
