import json
import os
import time
from pathlib import Path

import pytest

from saage.server.catalog import FlowCatalog
from saage.server.config import ServerConfig
from saage.server.jobs import JobRegistry

SLEEPER = ("provider: {type: local, model: m}\n"
           "shared: {seconds: '30'}\n"
           "workflow:\n  - {id: nap, type: command, run: 'sleep {{ seconds }}'}\n")


@pytest.fixture
def flow_info(tmp_path):
    d = tmp_path / "flows" / "sleeper"
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(SLEEPER)
    cat = FlowCatalog(ServerConfig(flow_paths=[tmp_path / "flows"]))
    cat.refresh()
    return cat.get("sleeper")


def test_unknown_override_rejected(flow_info):
    reg = JobRegistry()
    with pytest.raises(ValueError, match="nonsense"):
        reg.launch(flow_info, {"nonsense": "1"})


def test_launch_status_cancel_roundtrip(flow_info):
    reg = JobRegistry()
    job = reg.launch(flow_info, {"seconds": "30"})
    assert reg.status(job.job_id) == "running"
    assert reg.get(job.job_id)["overrides"] == {"seconds": "30"}
    assert reg.cancel(job.job_id)
    deadline = time.time() + 10
    while time.time() < deadline and reg.status(job.job_id) == "running":
        time.sleep(0.2)
    assert reg.status(job.job_id) == "cancelled"
    with pytest.raises(OSError):
        os.kill(job.pid, 0)               # process group is gone


def test_registry_survives_restart(flow_info):
    job = JobRegistry().launch(flow_info, {})
    reg2 = JobRegistry()                   # fresh instance, same SAAGE_HOME
    assert reg2.get(job.job_id)["flow_name"] == "sleeper"
    reg2.cancel(job.job_id)
