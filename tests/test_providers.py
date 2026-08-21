"""make_provider wiring for OpenAI-compatible providers (offline, no network)."""
import pytest

from saage.hydrate import make_provider
from saage.llm import OpenAIProvider, ProviderKeyError


def test_nvidia_provider_base_url_and_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    p = make_provider({"type": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"})
    assert isinstance(p, OpenAIProvider)
    assert p.model == "nvidia/nemotron-3-ultra-550b-a55b"
    # assert on saage's resolved wiring, not the openai client's internals
    assert p.base_url == "https://integrate.api.nvidia.com/v1"
    assert p.api_key_env == "NVIDIA_API_KEY"


# ------------------------------------------------------------------------- #
# key preflight: a missing key fails at build time with a message naming the
# env var, instead of a 401 traceback mid-run
# ------------------------------------------------------------------------- #

@pytest.mark.parametrize("ptype,env", [
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("nvidia", "NVIDIA_API_KEY"),
])
def test_missing_key_fails_fast_and_names_the_var(monkeypatch, ptype, env):
    monkeypatch.delenv(env, raising=False)
    with pytest.raises(ProviderKeyError, match=env):
        make_provider({"type": ptype, "model": "m"})


def test_local_provider_needs_no_key(monkeypatch):
    monkeypatch.delenv("LOCAL_API_KEY", raising=False)
    p = make_provider({"type": "local", "model": "llama3.1:8b"})
    assert isinstance(p, OpenAIProvider)


def test_custom_api_key_env_is_respected(monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    with pytest.raises(ProviderKeyError, match="MY_KEY"):
        make_provider({"type": "nvidia", "model": "m", "api_key_env": "MY_KEY"})
    monkeypatch.setenv("MY_KEY", "k")
    assert make_provider({"type": "nvidia", "model": "m",
                          "api_key_env": "MY_KEY"}).api_key_env == "MY_KEY"


def test_key_present_constructs(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    p = make_provider({"type": "openrouter", "model": "openai/gpt-4o-mini"})
    assert p.api_key_env == "OPENROUTER_API_KEY"


def test_command_only_flow_builds_without_key(tmp_path, monkeypatch):
    # a flow with no agent steps never calls the provider — keyless build stays legal
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from saage.hydrate import build_flow
    flow = tmp_path / "flow.yaml"
    flow.write_text(
        "provider: { type: openai, model: x }\n"
        "workflow:\n"
        '  - { id: say, type: command, run: "echo hi" }\n')
    build_flow(flow, workspace=str(tmp_path / "ws"))


def test_agent_flow_preflights_key_even_when_nested(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from saage.hydrate import build_flow
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "skill.md").write_text("---\n---\nSKILL_ID: s\n")
    flow = tmp_path / "flow.yaml"
    flow.write_text(
        "provider: { type: openai, model: x }\n"
        "workflow:\n"
        "  - id: loop\n"
        "    type: counting_loop\n"
        "    max_iterations: 2\n"
        "    body:\n"
        "      - { id: a, type: agent, skill: s }\n")
    with pytest.raises(ProviderKeyError, match="OPENAI_API_KEY"):
        build_flow(flow, workspace=str(tmp_path / "ws"))
