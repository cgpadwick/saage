# Web Search Tool — Design

**Status:** approved (2026-06-23). Closes #28.

## Goal

Give saage agents a `web_search` harness tool. **Keyless by default** (DuckDuckGo,
no API key) so it works out-of-box, with **optional keyed backends** (Tavily,
Brave) for reliability/volume. Provider-agnostic (a saage tool in the tool-use
loop, not a provider-native feature), opt-in per skill via the `tools:` allow-list.

## Architecture

A new module `saage/search.py` holds the backends + dispatcher; `tools.py` wraps
it as a `web_search` Tool and adds it to `default_tools()`.

```
web_search(query, max_results=5)                 # Tool fn in tools.py
  -> saage.search.web_search(query, max_results) # dispatcher
       -> pick backend from env (auto|ddg|tavily|brave)
       -> backend(query, max_results, *, fetch=<injectable>) -> list[Result]
       -> _format(results) -> str   (title · url · snippet [+ answer])
  any failure / no backend -> "ERROR: <reason+fix>"  (tool-error contract)
```

- **`Result`**: a small dataclass `{title, url, snippet}`. Tavily may also yield a
  synthesized `answer` string, surfaced at the top of the formatted text.
- **Backends are pure functions** with the network call injected as a default-arg
  callable (`fetch`), so tests replace it with a canned response — no key, no net.

## Backend selection (`SAAGE_SEARCH_BACKEND`, default `auto`)

`auto`: Tavily if `TAVILY_API_KEY` set → else Brave if `BRAVE_API_KEY` set → else
DuckDuckGo (keyless). An explicit value (`ddg`/`tavily`/`brave`) forces that one.

- **ddg** — DuckDuckGo via the optional `ddgs` lib. Keyless. If `ddgs` isn't
  installed → `ERROR: web search needs the 'ddgs' package (pip install saage[search])
  or a TAVILY_API_KEY/BRAVE_API_KEY`.
- **tavily** — `POST https://api.tavily.com/search` (stdlib `urllib`, no dep),
  `TAVILY_API_KEY`. Returns results + an `answer`.
- **brave** — `GET https://api.search.brave.com/res/v1/web/search` (header
  `X-Subscription-Token`), `BRAVE_API_KEY`.
- A keyed backend selected without its key → graceful `ERROR: set <KEY> …` (never
  calls the network).

## Tool contract

- name: `web_search`; params: `query: str` (required), `max_results: int = 5`.
- returns: a plain-text block the agent reads, e.g.

  ```
  Answer: <tavily answer, if any>

  1. <title>
     <url>
     <snippet>
  2. ...
  ```
- never raises: any backend/network/parse failure returns an `ERROR: …` string
  (the agent reacts, the run continues — same contract as the other tools).

## Safety

Network egress. Note the engine's allow-list semantics: `web_search` is part of
`default_tools()`, so a skill with **no `tools:` allow-list can call it** (omitting
`tools:` = all tools). It is therefore "opt-out", not opt-in: to keep a skill off
the network, give it a `tools:` allow-list that omits `web_search` (and
`run_command`). The allow-list (validated by #20/#26) is the control — there's no
implicit network sandbox. Not driven by the `run_command` denylist (structured
tool, no shell).

## Dependencies

`ddgs` is an **optional extra**: `pip install saage[search]`. Core deps unchanged;
Tavily/Brave use stdlib `urllib` (no dep). `saage/search.py` imports `ddgs`
lazily inside the ddg backend so the module imports fine without it.

## Testing (all offline, no key, no network)

`tests/test_search.py`:
1. **Parse/format** — `tavily_search`/`brave_search` with an injected `fetch`
   returning a canned (documented-shape) JSON payload → assert normalized
   `Result`s (+ Tavily answer). `ddg_search` with an injected fake `ddgs` result
   list → assert normalized.
2. **No-key branches** — backend=tavily/brave without the key → returns the
   graceful `ERROR: set <KEY>` and never calls `fetch`.
3. **Selection** — env permutations → assert the right backend is chosen, incl.
   `auto` falling back to ddg.
4. **ddgs-missing** — ddg backend with the import unavailable → graceful ERROR.
5. **format** — `_format` of normalized results → the agent-facing text block;
   empty results → a clear "no results" message.
6. **Optional live** — a `@pytest.mark.live` test that hits the real Tavily/Brave
   ONLY when a key is present (mirrors the repo's `live`/`SAAGE_LIVE_PROVIDER`
   marker); skipped in normal CI.

Also: `tests/test_flows_hydrate.py` and `default_tools()` must still load with the
new tool present.

## Out of scope (follow-up)

- `fetch_url(url)` — read a page's content (search → read). Natural pair; separate.
- Result caching / rate-limit backoff for the keyless DDG path.
