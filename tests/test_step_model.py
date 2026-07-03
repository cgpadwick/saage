"""Per-step `model:` override on agent steps (offline, no network).

An agent step may name a `model:`; hydrate then builds a same-type provider
for that model from the flow's provider spec. Steps sharing a model share one
provider instance; a CLI --model (provider_overrides) forces one model
everywhere; an injected provider object (tests) ignores step models.
"""
from pathlib import Path

import yaml

from saage.hydrate import Context, build_flow


def _write_flow(tmp_path: Path, step_model: bool) -> Path:
    d = tmp_path / "flow"
    (d / "hello").mkdir(parents=True)
    (d / "hello" / "skill.md").write_text(
        "---\ndescription: say hello\n---\nSay hello.\n", encoding="utf-8")
    step = {"id": "s1", "type": "agent", "skill": "hello"}
    if step_model:
        step["model"] = "org/step-model"
    spec = {
        "provider": {"type": "openrouter", "model": "org/flow-model"},
        "workflow": [step,
                     {"id": "s2", "type": "agent", "skill": "hello"}],
    }
    (d / "flow.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return d / "flow.yaml"


def _agent_nodes(flow):
    """The two top-level AgentNodes, in step order."""
    n1 = flow.start_node
    n2 = n1.successors["default"]
    return n1, n2


def test_step_model_builds_override_provider(tmp_path):
    flow, _ = build_flow(_write_flow(tmp_path, step_model=True),
                         workspace=str(tmp_path / "ws"))
    s1, s2 = _agent_nodes(flow)
    assert s1.provider.model == "org/step-model"
    assert s2.provider.model == "org/flow-model"
    assert type(s1.provider) is type(s2.provider)   # same provider type
    assert s1.provider is not s2.provider


def test_step_model_shares_cached_provider(tmp_path):
    ctx_flow = _write_flow(tmp_path, step_model=True)
    spec = yaml.safe_load(ctx_flow.read_text())
    spec["workflow"][1]["model"] = "org/step-model"   # both steps: same override
    ctx_flow.write_text(yaml.safe_dump(spec))
    flow, _ = build_flow(ctx_flow, workspace=str(tmp_path / "ws"))
    s1, s2 = _agent_nodes(flow)
    assert s1.provider is s2.provider                 # one client per model


def test_cli_model_override_wins_over_step_model(tmp_path):
    flow, _ = build_flow(_write_flow(tmp_path, step_model=True),
                         provider_overrides={"model": "org/forced"},
                         workspace=str(tmp_path / "ws"))
    s1, s2 = _agent_nodes(flow)
    assert s1.provider.model == "org/forced"
    assert s1.provider is s2.provider


def test_injected_provider_ignores_step_model(tmp_path):
    sentinel = object()
    flow, _ = build_flow(_write_flow(tmp_path, step_model=True),
                         provider=sentinel, workspace=str(tmp_path / "ws"))
    s1, s2 = _agent_nodes(flow)
    assert s1.provider is sentinel and s2.provider is sentinel


def test_step_provider_without_model_is_flow_provider():
    ctx = Context(root=Path("."), provider="P", skills={}, tools=[],
                  pspec={"type": "openrouter", "model": "m"})
    assert ctx.step_provider(None) == "P"
