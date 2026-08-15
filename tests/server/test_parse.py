"""Natural-language launch parsing tests."""
import pytest

from saage.llm import LLMResponse, ScriptedProvider
from saage.server.catalog import FlowCatalog
from saage.server.config import ServerConfig
from saage.server.parse import parse_launch


GOOD = ("# A demo flow.\n# Second line ignored.\n"
        "provider: {type: local, model: m}\n"
        "shared: {knob_a: '1', knob_b: hello}\n"
        "workflow:\n  - {id: s1, type: command, run: 'echo hi'}\n")


def _mk(tmp_path, name, text):
    d = tmp_path / "flows" / name
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(text)


@pytest.fixture
def catalog(tmp_path):
    _mk(tmp_path, "demo", GOOD)
    cat = FlowCatalog(ServerConfig(flow_paths=[tmp_path / "flows"]))
    cat.refresh()
    return cat


def test_valid_plan_passes(catalog):
    p = ScriptedProvider([LLMResponse('{"flow": "demo", "overrides": {"knob_a": "5"}, '
                                      '"explanation": "sets knob_a"}')])
    out = parse_launch("run demo with knob_a five", catalog, p)
    assert out == {"ok": True, "flow": "demo", "overrides": {"knob_a": "5"},
                   "explanation": "sets knob_a"}


def test_unknown_flow_rejected(catalog):
    p = ScriptedProvider([LLMResponse('{"flow": "ghost", "overrides": {}, "explanation": "x"}')])
    out = parse_launch("run ghost", catalog, p)
    assert not out["ok"] and "ghost" in out["error"]


def test_unknown_knob_rejected(catalog):
    p = ScriptedProvider([LLMResponse('{"flow": "demo", "overrides": {"batch": "2"}, '
                                      '"explanation": "x"}')])
    out = parse_launch("sweep batch", catalog, p)
    assert not out["ok"] and "batch" in out["error"]


def test_non_json_rejected(catalog):
    out = parse_launch("hi", catalog, ScriptedProvider([LLMResponse("sure! I will run demo")]))
    assert not out["ok"] and "JSON" in out["error"]
