"""make_provider wiring for OpenAI-compatible providers (offline, no network)."""
from saage.hydrate import make_provider
from saage.llm import OpenAIProvider


def test_nvidia_provider_base_url_and_key():
    p = make_provider({"type": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"})
    assert isinstance(p, OpenAIProvider)
    assert p.model == "nvidia/nemotron-3-ultra-550b-a55b"
    # assert on saage's resolved wiring, not the openai client's internals
    assert p.base_url == "https://integrate.api.nvidia.com/v1"
    assert p.api_key_env == "NVIDIA_API_KEY"
