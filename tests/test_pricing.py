"""Token-cost estimates come ONLY from user-configured SAAGE_PRICES rates:
no built-in table (it went stale and mis-priced runs), unknown -> None."""
import json

from saage.pricing import cost, rates


def test_no_builtin_rates(monkeypatch):
    monkeypatch.delenv("SAAGE_PRICES", raising=False)
    for m in ("deepseek/deepseek-v4-flash", "anthropic/claude-sonnet-4-6",
              "openai/gpt-4o-mini", "some-random-local-model", ""):
        assert rates(m) is None
        assert cost(m, 1_000_000, 1_000_000) is None


def _prices(tmp_path, monkeypatch, table):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(table))
    monkeypatch.setenv("SAAGE_PRICES", str(p))


def test_configured_rates_substring_match(tmp_path, monkeypatch):
    _prices(tmp_path, monkeypatch, {"deepseek": [0.27, 1.10], "claude-sonnet": [3.0, 15.0]})
    assert rates("deepseek/deepseek-v4-flash") == (0.27, 1.10)
    assert rates("anthropic/claude-sonnet-4-6") == (3.0, 15.0)
    assert rates("mystery") is None


def test_longest_key_wins(tmp_path, monkeypatch):
    _prices(tmp_path, monkeypatch, {"gpt-4o": [2.50, 10.0], "gpt-4o-mini": [0.15, 0.60]})
    assert rates("openai/gpt-4o-mini") == (0.15, 0.60)
    assert rates("gpt-4o-2024-11") == (2.50, 10.0)


def test_cost_math(tmp_path, monkeypatch):
    _prices(tmp_path, monkeypatch, {"deepseek": [0.27, 1.10]})
    # 1M input @ 0.27 + 1M output @ 1.10 = 1.37 USD
    assert abs(cost("deepseek-x", 1_000_000, 1_000_000) - 1.37) < 1e-9
    assert cost("deepseek-x", 0, 0) == 0.0
    assert cost("mystery-model", 100, 100) is None


def test_unreadable_override_is_ignored(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    monkeypatch.setenv("SAAGE_PRICES", str(p))
    assert rates("deepseek/x") is None            # nothing to fall back to


def test_malformed_entry_skipped_not_fatal(tmp_path, monkeypatch):
    _prices(tmp_path, monkeypatch, {"good": [1.0, 2.0], "bad": "oops", "short": [1.0]})
    assert rates("good-model") == (1.0, 2.0)
    assert rates("bad-model") is None


def test_mixed_case_override_key_matches(tmp_path, monkeypatch):
    _prices(tmp_path, monkeypatch, {"DeepSeek": [7.0, 8.0]})   # mixed-case key
    # rates() lowercases the model id; the key is normalized too, so it hits
    assert rates("deepseek/deepseek-v4") == (7.0, 8.0)
