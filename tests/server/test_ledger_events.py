"""Ledger start/end events: saage/server needs 'running' nodes, so _orch
appends a start line before each node runs and tags completion lines end."""
import json

from saage import checkpoint as ckpt
from saage.hydrate import run_flow


def _write_flow(tmp_path):
    (tmp_path / "flow.yaml").write_text(
        "provider: {type: local, model: m}\n"
        "workflow:\n"
        "  - {id: hello, type: command, run: 'echo hi'}\n"
        "  - {id: world, type: command, run: 'echo there'}\n")
    return tmp_path / "flow.yaml"


def test_ledger_has_start_and_end_phases(tmp_path):
    flow = _write_flow(tmp_path)
    run = ckpt.Checkpoint.create(ckpt.new_run_id(), flow_path=str(flow))
    run_flow(flow, provider=object(), workspace=tmp_path, checkpoint=run)
    lines = [json.loads(x) for x in (run.dir / "ledger.jsonl").read_text().splitlines()]
    hello = [e for e in lines if e["node"] == "hello"]
    phases = [e.get("phase") for e in hello]
    assert "start" in phases and "end" in phases
    assert phases.index("start") < phases.index("end")
    start = next(e for e in hello if e.get("phase") == "start")
    assert "action" not in start          # start events carry no outcome fields
    end = next(e for e in hello if e.get("phase") == "end")
    assert end["exit"] == 0
