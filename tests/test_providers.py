"""make_provider wiring for OpenAI-compatible providers (offline, no network)."""
from saage.hydrate import make_provider
from saage.llm import AnthropicProvider, OpenAIProvider


def test_anthropic_provider_default_max_tokens(monkeypatch):
    # 4096 truncated long tool calls (e.g. an agent writing a multi-page
    # report in one write_file) — the default must be comfortably larger.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    p = make_provider({"type": "anthropic", "model": "claude-sonnet-4-6"})
    assert isinstance(p, AnthropicProvider)
    assert p.max_tokens >= 16384


def test_anthropic_provider_max_tokens_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    p = make_provider({"type": "anthropic", "model": "claude-sonnet-4-6",
                       "max_tokens": 32000})
    assert p.max_tokens == 32000


def test_nvidia_provider_base_url_and_key():
    p = make_provider({"type": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"})
    assert isinstance(p, OpenAIProvider)
    assert p.model == "nvidia/nemotron-3-ultra-550b-a55b"
    # assert on saage's resolved wiring, not the openai client's internals
    assert p.base_url == "https://integrate.api.nvidia.com/v1"
    assert p.api_key_env == "NVIDIA_API_KEY"
