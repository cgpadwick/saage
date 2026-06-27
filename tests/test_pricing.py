"""Token-cost pricing: substring matching, cost math, unknown -> None, override."""
import json

from saage.pricing import cost, rates


def test_rates_substring_match():
    assert rates("deepseek/deepseek-v4-flash") == (0.27, 1.10)
    assert rates("anthropic/claude-sonnet-4-6") == (3.0, 15.0)


def test_rates_longest_key_wins():
    # "gpt-4o-mini" must win over "gpt-4o"
    assert rates("openai/gpt-4o-mini") == (0.15, 0.60)
    assert rates("gpt-4o-2024-11") == (2.50, 10.0)


def test_rates_unknown_is_none():
    assert rates("some-random-local-model") is None
    assert rates("") is None


def test_cost_math():
    # 1M input @ 0.27 + 1M output @ 1.10 = 1.37 USD
    assert abs(cost("deepseek-x", 1_000_000, 1_000_000) - 1.37) < 1e-9
    assert cost("deepseek-x", 0, 0) == 0.0


def test_cost_unknown_model_is_none():
    assert cost("mystery-model", 100, 100) is None


def test_env_override_wins(tmp_path, monkeypatch):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"mystery": [1.0, 2.0]}))
    monkeypatch.setenv("SAAGE_PRICES", str(p))
    assert rates("mystery-model") == (1.0, 2.0)
    assert cost("mystery", 1_000_000, 0) == 1.0


def test_unreadable_override_is_ignored(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    monkeypatch.setenv("SAAGE_PRICES", str(p))
    assert rates("deepseek/x") == (0.27, 1.10)   # falls back to the built-in table


def test_override_replaces_builtin(tmp_path, monkeypatch):
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"deepseek": [9.0, 9.0]}))
    monkeypatch.setenv("SAAGE_PRICES", str(p))
    assert rates("deepseek/deepseek-v4-flash") == (9.0, 9.0)   # override wins


def test_mixed_case_override_key_matches(tmp_path, monkeypatch):
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"DeepSeek": [7.0, 8.0]}))   # mixed-case key
    monkeypatch.setenv("SAAGE_PRICES", str(p))
    # rates() lowercases the model id; the override key is normalized too, so it hits
    assert rates("deepseek/deepseek-v4") == (7.0, 8.0)


def test_one_malformed_entry_keeps_the_rest(tmp_path, monkeypatch):
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"good-model": [1.0, 2.0], "bad-model": [3.0]}))  # 1-elem
    monkeypatch.setenv("SAAGE_PRICES", str(p))
    assert rates("good-model-x") == (1.0, 2.0)   # well-formed entry survives
    assert rates("bad-model-x") is None          # malformed entry skipped, not fatal
