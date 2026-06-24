"""web_search backends + dispatcher — fully offline (injected fetch / search_fn,
no API key, no network)."""
import pytest

from saage import search
from saage.search import (Result, _format, brave_search, ddg_search,
                          tavily_search, web_search)

TAVILY_JSON = {
    "answer": "Paris is the capital of France.",
    "results": [
        {"title": "France", "url": "https://en.wikipedia.org/wiki/France",
         "content": "France is a country in Europe."},
        {"title": "Paris", "url": "https://example.com/paris",
         "content": "Paris is the capital."},
    ],
}
BRAVE_JSON = {"web": {"results": [
    {"title": "France", "url": "https://en.wikipedia.org/wiki/France",
     "description": "country in Europe"},
]}}
DDG_RAW = [{"title": "France", "href": "https://en.wikipedia.org/wiki/France",
            "body": "country in Europe"}]


def test_tavily_parses_results_and_answer():
    results, answer = tavily_search("capital of france", api_key="x",
                                    fetch=lambda url, **kw: TAVILY_JSON)
    assert answer == "Paris is the capital of France."
    assert results[0] == Result("France", "https://en.wikipedia.org/wiki/France",
                                "France is a country in Europe.")
    assert len(results) == 2


def test_tavily_no_key_never_fetches(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    called = []
    with pytest.raises(search._NoKey):
        tavily_search("q", fetch=lambda *a, **k: called.append(1) or {})
    assert not called                       # no network call without a key


def test_brave_parses_results():
    results, answer = brave_search("france", api_key="x",
                                   fetch=lambda url, **kw: BRAVE_JSON)
    assert answer == ""
    assert results[0].url == "https://en.wikipedia.org/wiki/France"


def test_ddg_parses_results():
    results, _ = ddg_search("france", search_fn=lambda q, n: DDG_RAW)
    assert results[0] == Result("France", "https://en.wikipedia.org/wiki/France",
                                "country in Europe")


def test_ddg_missing_lib_is_no_backend():
    def _missing(q, n):
        raise ImportError("no ddgs")
    with pytest.raises(search._NoBackend):
        ddg_search("q", search_fn=_missing)


def test_select_backend(monkeypatch):
    monkeypatch.delenv("SAAGE_SEARCH_BACKEND", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert search.select_backend() == "ddg"             # keyless default
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    assert search.select_backend() == "brave"
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    assert search.select_backend() == "tavily"          # tavily wins in auto
    monkeypatch.setenv("SAAGE_SEARCH_BACKEND", "ddg")
    assert search.select_backend() == "ddg"             # explicit override wins


def test_web_search_no_key_returns_graceful_error(monkeypatch):
    monkeypatch.setenv("SAAGE_SEARCH_BACKEND", "tavily")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    out = web_search("q")
    assert out.startswith("ERROR:") and "TAVILY_API_KEY" in out


def test_web_search_formats_results(monkeypatch):
    monkeypatch.setenv("SAAGE_SEARCH_BACKEND", "tavily")
    monkeypatch.setitem(search._BACKENDS, "tavily",
                        lambda q, n=5: ([Result("T", "http://u", "snip")], "ans"))
    out = web_search("q")
    assert "Answer: ans" in out and "1. T" in out and "http://u" in out


def test_web_search_clamps_and_coerces_max_results(monkeypatch):
    seen = {}
    def fake(q, n):
        seen["n"] = n
        return [], ""
    monkeypatch.setenv("SAAGE_SEARCH_BACKEND", "ddg")
    monkeypatch.setitem(search._BACKENDS, "ddg", fake)
    web_search("q", max_results="5.0"); assert seen["n"] == 5     # non-int -> default
    web_search("q", max_results=None);  assert seen["n"] == 5     # null -> default
    web_search("q", max_results=999);   assert seen["n"] == 20    # huge -> clamp
    web_search("q", max_results=0);     assert seen["n"] == 1     # <1 -> clamp


def test_web_search_unknown_backend_errors(monkeypatch):
    monkeypatch.setenv("SAAGE_SEARCH_BACKEND", "bogus")
    assert web_search("q").startswith("ERROR: unknown SAAGE_SEARCH_BACKEND")


def test_format_empty_results():
    assert "No web results" in _format("xyz", [], "")


def test_web_search_is_in_default_tools(tmp_path):
    from saage.tools import default_tools
    names = {t.name for t in default_tools(tmp_path)}
    assert "web_search" in names
