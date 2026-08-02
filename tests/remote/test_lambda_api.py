import urllib.error
import urllib.request

import pytest

from saage.remote.lambda_api import (LambdaAPI, LambdaError, pick_instance_type,
                                     wait_active)


def _avail(**types):
    """types: name=(cents_per_hour, [regions])"""
    return {
        name: {
            "instance_type": {"price_cents_per_hour": cents},
            "regions_with_capacity_available": [{"name": r} for r in regions],
        }
        for name, (cents, regions) in types.items()
    }


def test_auto_picks_cheapest_with_capacity():
    avail = _avail(gpu_1x_h100_pcie=(249, ["us-east-1"]),
                   gpu_1x_a10=(75, ["us-west-1"]),
                   gpu_1x_a100=(129, []))           # no capacity -> skipped
    assert pick_instance_type(avail, "auto") == ("gpu_1x_a10", "us-west-1", 0.75)


def test_gpu_class_preference_order():
    avail = _avail(gpu_1x_a100_sxm4=(129, []),       # preferred but no capacity
                   gpu_1x_a100=(110, ["us-east-1"]))
    assert pick_instance_type(avail, "a100") == ("gpu_1x_a100", "us-east-1", 1.10)


def test_exact_type_name_works():
    avail = _avail(gpu_1x_gh200=(149, ["us-east-3"]))
    assert pick_instance_type(avail, "gpu_1x_gh200") == ("gpu_1x_gh200", "us-east-3", 1.49)


def test_no_capacity_error_lists_alternatives():
    avail = _avail(gpu_1x_a10=(75, []), gpu_1x_h100_pcie=(249, ["us-east-1"]))
    with pytest.raises(LambdaError, match="gpu_1x_h100_pcie"):
        pick_instance_type(avail, "a10")


# --------------------------------------------------------------------------- #
# wait_active: never leak a billing instance
# --------------------------------------------------------------------------- #
def test_wait_active_tolerates_transient_poll_errors():
    # a 5xx/network blip mid-poll must NOT abort the wait (aborting would
    # leak a running instance) — only the wall-clock deadline gives up
    class FlakyAPI:
        calls = 0
        def instance(self, iid):
            FlakyAPI.calls += 1
            if FlakyAPI.calls < 3:
                raise LambdaError("Lambda API /instances/i-1 -> 502: bad gateway")
            return {"status": "active", "ip": "1.2.3.4"}
        def terminate(self, ids):
            raise AssertionError("must not terminate on a transient poll error")

    inst = wait_active(FlakyAPI(), "i-1", timeout_s=30, poll_interval=0)
    assert inst["ip"] == "1.2.3.4"


def test_wait_active_terminates_on_timeout():
    class NeverActive:
        terminated = None
        def instance(self, iid):
            return {"status": "booting"}
        def terminate(self, ids):
            NeverActive.terminated = ids

    with pytest.raises(LambdaError, match="not active"):
        wait_active(NeverActive(), "i-2", timeout_s=0, poll_interval=0)
    assert NeverActive.terminated == ["i-2"]


# --------------------------------------------------------------------------- #
# ensure_ssh_key: content-aware — a second machine's saage key must not be
# shadowed by the same NAME registered from another machine (the instance
# would boot authorized for the wrong key and ssh would never come up)
# --------------------------------------------------------------------------- #
class _KeyAPI(LambdaAPI):
    def __init__(self, registered):
        super().__init__("key")
        self.registered = registered            # list of {"name", "public_key"}
        self.uploads = []

    def ssh_keys(self):
        return self.registered

    def _request(self, path, payload=None):
        assert path == "/ssh-keys"
        self.uploads.append(payload)
        self.registered.append(payload)
        return {"data": payload}


_KEY_A = "ssh-ed25519 AAAA_machine_A saage-remote"
_KEY_B = "ssh-ed25519 AAAA_machine_B saage-remote"


def test_ensure_ssh_key_registers_when_absent():
    api = _KeyAPI([])
    assert api.ensure_ssh_key("saage-remote", _KEY_A) == "saage-remote"
    assert len(api.uploads) == 1


def test_ensure_ssh_key_reuses_matching_key_ignoring_comment():
    api = _KeyAPI([{"name": "saage-remote",
                    "public_key": "ssh-ed25519 AAAA_machine_A other-comment"}])
    assert api.ensure_ssh_key("saage-remote", _KEY_A) == "saage-remote"
    assert api.uploads == []


def test_ensure_ssh_key_name_taken_by_other_machine_registers_variant():
    api = _KeyAPI([{"name": "saage-remote", "public_key": _KEY_A}])
    name = api.ensure_ssh_key("saage-remote", _KEY_B)
    assert name != "saage-remote"                 # never launch with the wrong key
    assert name.startswith("saage-remote-")
    assert len(api.uploads) == 1


def test_ensure_ssh_key_variant_is_stable_and_reused():
    api = _KeyAPI([{"name": "saage-remote", "public_key": _KEY_A}])
    first = api.ensure_ssh_key("saage-remote", _KEY_B)
    again = api.ensure_ssh_key("saage-remote", _KEY_B)
    assert again == first
    assert len(api.uploads) == 1                  # second call reuses, no re-upload


def test_wait_ssh_fails_fast_on_permission_denied(monkeypatch):
    # auth rejection is deterministic — looping the full timeout just hides the
    # real problem (wrong key registered) behind "ssh never came up"
    import subprocess as sp
    from saage.remote.lambda_api import wait_ssh
    calls = []

    def fake_run(cmd, capture_output=True, **kw):
        calls.append(cmd)
        if cmd[0] == "ssh-keygen":
            return sp.CompletedProcess(cmd, 0, b"", b"")
        return sp.CompletedProcess(cmd, 255, b"", b"Permission denied (publickey).")

    monkeypatch.setattr(sp, "run", fake_run)
    with pytest.raises(LambdaError, match="[Pp]ermission denied"):
        wait_ssh("1.2.3.4", "ubuntu", "/k", timeout_s=300)
    assert len([c for c in calls if c[0] == "ssh"]) == 1   # no 300s retry loop


def test_network_errors_become_lambda_errors(monkeypatch):
    # URLError (DNS/conn refused) must be wrapped like HTTPError, or it escapes
    # every `except LambdaError` net (incl. wait_active's transient tolerance)
    def boom(req, timeout=0):
        raise urllib.error.URLError("dns fail")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(LambdaError, match="dns fail"):
        LambdaAPI("key").instances()
