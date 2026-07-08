import urllib.error
import urllib.request

import pytest

from saage.remote.thunder_api import (ThunderAPI, ThunderError, pick_gpu,
                                      wait_running)


class _FakeAPI:
    """Static availability/pricing; per-test instance behavior."""
    def __init__(self, avail=None, prices=None):
        self._avail = avail or {}
        self._prices = prices or {}

    def availability(self):
        return self._avail

    def pricing(self):
        return self._prices


AVAIL = {"a6000": {"1": "available"}, "l40": {"1": "available"},
         "h100": {"1": "unavailable"}}
PRICES = {"a6000_x1": 0.35, "l40_x1": 0.79, "h100_x1": 2.19}


def test_auto_picks_cheapest_with_capacity():
    assert pick_gpu(_FakeAPI(AVAIL, PRICES), "auto") == ("a6000", 0.35)


def test_exact_type_works():
    assert pick_gpu(_FakeAPI(AVAIL, PRICES), "l40") == ("l40", 0.79)


def test_no_capacity_error_lists_alternatives():
    with pytest.raises(ThunderError, match="a6000"):
        pick_gpu(_FakeAPI(AVAIL, PRICES), "h100")


# --------------------------------------------------------------------------- #
# wait_running: never leak a billing instance
# --------------------------------------------------------------------------- #
def test_wait_running_tolerates_transient_poll_errors():
    class Flaky:
        calls = 0
        def instances(self, update_ips=False):
            Flaky.calls += 1
            if Flaky.calls < 3:
                raise ThunderError("Thunder API /instances/list -> 502: bad")
            return {"i-1": {"status": "RUNNING", "ip": "1.2.3.4"}}
        def delete(self, iid):
            raise AssertionError("must not delete on a transient poll error")

    inst = wait_running(Flaky(), "i-1", timeout_s=30, poll_interval=0)
    assert inst["ip"] == "1.2.3.4"


def test_wait_running_deletes_on_timeout():
    class NeverUp:
        deleted = None
        def instances(self, update_ips=False):
            return {"i-2": {"status": "STARTING"}}
        def delete(self, iid):
            NeverUp.deleted = iid

    with pytest.raises(ThunderError, match="not RUNNING"):
        wait_running(NeverUp(), "i-2", timeout_s=0, poll_interval=0)
    assert NeverUp.deleted == "i-2"


def test_wait_running_raises_when_instance_dies_during_boot():
    class Died:
        def instances(self, update_ips=False):
            return {"i-3": {"status": "FAILED"}}

    with pytest.raises(ThunderError, match="FAILED"):
        wait_running(Died(), "i-3", timeout_s=30, poll_interval=0)


def test_network_errors_become_thunder_errors(monkeypatch):
    def boom(req, timeout=0):
        raise urllib.error.URLError("dns fail")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ThunderError, match="dns fail"):
        ThunderAPI("tok").instances()


def test_instances_empty_response_is_empty_dict(monkeypatch):
    class R:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"null"
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: R())
    assert ThunderAPI("tok").instances() == {}


def test_create_retries_with_server_listed_vcpus(monkeypatch):
    # per-GPU vCPU validation lives server-side only — a rejected count must
    # retry once with the smallest count the error lists (seen live: a6000)
    calls = []

    def fake_request(self, path, payload=None):
        calls.append(payload["cpu_cores"])
        if payload["cpu_cores"] not in (4, 6) or len(calls) == 1 and payload["cpu_cores"] == 8:
            raise ThunderError('Thunder API /instances/create -> 400: '
                               '{"message":"validation: invalid vCPU count 8; '
                               'valid options: [4 6]"}')
        return {"identifier": "i-9", "key": "PEM"}

    monkeypatch.setattr(ThunderAPI, "_request", fake_request)
    iid, key = ThunderAPI("tok").create("a6000", cpu_cores=8)
    assert (iid, key) == ("i-9", "PEM")
    assert calls == [8, 4]


def test_create_non_vcpu_error_propagates(monkeypatch):
    def fake_request(self, path, payload=None):
        raise ThunderError("Thunder API /instances/create -> 402: payment required")
    monkeypatch.setattr(ThunderAPI, "_request", fake_request)
    with pytest.raises(ThunderError, match="402"):
        ThunderAPI("tok").create("a6000")
