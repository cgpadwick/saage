"""make_provider wiring for OpenAI-compatible providers (offline, no network)."""
from saage.hydrate import make_provider
from saage.llm import OpenAIProvider


def test_nvidia_provider_base_url_and_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-123")
    p = make_provider({"type": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"})
    assert isinstance(p, OpenAIProvider)
    assert p.model == "nvidia/nemotron-3-ultra-550b-a55b"
    # openai client exposes base_url (httpx URL) and api_key
    assert str(p.client.base_url).startswith("https://integrate.api.nvidia.com/v1")
    assert p.client.api_key == "test-key-123"
