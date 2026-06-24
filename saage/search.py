"""Web-search backends for the `web_search` harness tool.

Keyless DuckDuckGo by default (the optional `ddgs` package); optional keyed
Tavily / Brave for reliability and volume. Each backend is a pure function with
its network call injected as a default-arg callable, so the parsing/formatting is
fully testable offline (no key, no network). The public entry point `web_search`
NEVER raises — every failure becomes an `ERROR: …` string the agent can react to,
matching the rest of the harness's tool-error contract.

Backend selection (`SAAGE_SEARCH_BACKEND`, default `auto`):
  auto -> tavily if TAVILY_API_KEY, else brave if BRAVE_API_KEY, else ddg (keyless)
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass


@dataclass
class Result:
    title: str
    url: str
    snippet: str


class _NoKey(Exception):
    """A keyed backend was selected but its API key isn't set."""
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


class _NoBackend(Exception):
    """The keyless backend's dependency (ddgs) isn't installed."""


def _http_json(url: str, *, data: bytes | None = None,
               headers: dict | None = None, timeout: float = 20.0):
    """GET (data=None) or POST a URL and return parsed JSON. Injected in tests."""
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tavily_search(query: str, max_results: int = 5, *,
                  api_key: str | None = None, fetch=_http_json):
    key = api_key or os.environ.get("TAVILY_API_KEY")
    if not key:
        raise _NoKey("TAVILY_API_KEY")
    payload = json.dumps({"api_key": key, "query": query,
                          "max_results": max_results}).encode("utf-8")
    data = fetch("https://api.tavily.com/search", data=payload,
                 headers={"Content-Type": "application/json"})
    results = [Result(r.get("title", ""), r.get("url", ""), r.get("content", ""))
               for r in (data.get("results") or [])[:max_results]]
    return results, (data.get("answer") or "")


def brave_search(query: str, max_results: int = 5, *,
                 api_key: str | None = None, fetch=_http_json):
    key = api_key or os.environ.get("BRAVE_API_KEY")
    if not key:
        raise _NoKey("BRAVE_API_KEY")
    from urllib.parse import urlencode
    url = ("https://api.search.brave.com/res/v1/web/search?"
           + urlencode({"q": query, "count": max_results}))
    data = fetch(url, headers={"X-Subscription-Token": key,
                               "Accept": "application/json"})
    web = ((data.get("web") or {}).get("results")) or []
    results = [Result(r.get("title", ""), r.get("url", ""), r.get("description", ""))
               for r in web[:max_results]]
    return results, ""


def _ddgs_text(query: str, max_results: int):
    from ddgs import DDGS                       # optional dep; lazily imported
    with DDGS() as d:
        return list(d.text(query, max_results=max_results))


def ddg_search(query: str, max_results: int = 5, *, search_fn=_ddgs_text):
    try:
        raw = search_fn(query, max_results)
    except ImportError:
        raise _NoBackend(
            "web search needs the 'ddgs' package (pip install saage[search]) "
            "or a TAVILY_API_KEY / BRAVE_API_KEY")
    results = [Result(r.get("title", ""),
                      r.get("href") or r.get("url", ""),
                      r.get("body") or r.get("snippet", ""))
               for r in (raw or [])[:max_results]]
    return results, ""


_BACKENDS = {"tavily": tavily_search, "brave": brave_search, "ddg": ddg_search}


def select_backend() -> str:
    b = os.environ.get("SAAGE_SEARCH_BACKEND", "auto").lower()
    if b != "auto":
        return b
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    if os.environ.get("BRAVE_API_KEY"):
        return "brave"
    return "ddg"


def _format(query: str, results: list[Result], answer: str = "") -> str:
    if not results and not answer:
        return f"No web results for {query!r}."
    out = []
    if answer:
        out.append(f"Answer: {answer}\n")
    for i, r in enumerate(results, 1):
        out.append(f"{i}. {r.title}\n   {r.url}\n   {r.snippet}")
    return "\n".join(out)


_MAX_RESULTS_CAP = 20


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web; return a formatted text block of results, or an
    `ERROR: …` string (never raises). `max_results` is coerced to an int and
    clamped to [1, 20] so a stray null/"5.0"/negative/huge value can't turn into a
    backend error or odd slicing — the public entry point owns that, not callers."""
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(n, _MAX_RESULTS_CAP))
    name = select_backend()
    fn = _BACKENDS.get(name)
    if fn is None:
        return (f"ERROR: unknown SAAGE_SEARCH_BACKEND {name!r} "
                f"(use auto|ddg|tavily|brave)")
    try:
        results, answer = fn(query, n)
    except _NoKey as e:
        return (f"ERROR: web_search backend {name!r} needs {e.key} set in the "
                f"environment (or unset SAAGE_SEARCH_BACKEND to fall back to "
                f"keyless DuckDuckGo)")
    except _NoBackend as e:
        return f"ERROR: {e}"
    except Exception as e:                       # network / parse / rate-limit
        return f"ERROR: web_search ({name}) failed: {e}"
    return _format(query, results, answer)
