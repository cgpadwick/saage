"""Provider-agnostic LLM layer.

A neutral message/tool-call format keeps the agent loop (agent.py) independent of
any vendor. Each provider translates that neutral format to its own API.

Neutral history items the loop appends:
    {"role": "user",      "text": str}
    {"role": "assistant", "text": str, "tool_calls": [ToolCall, ...]}
    {"role": "tool",      "results": [(call_id, output_str), ...]}
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Protocol

from .retry import RetryPolicy, call_with_retry
from .tools import Tool

# provider.type -> env var expected to hold its API key. `local` servers need
# no key so they are absent here (saage.remote.creds keeps its own map with
# different semantics: which keys to push to a remote node).
PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}


class ProviderKeyError(RuntimeError):
    """The run has no usable provider: no provider configured anywhere (flow /
    `saage setup` defaults / CLI flags), or the chosen one's API key is neither
    in the environment nor saved by `saage setup`."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class EmptyResponseError(RuntimeError):
    """A provider returned HTTP 200 with no usable `choices` (an error body
    behind a 200 — seen live from OpenRouter). Named so retry.is_retryable_error
    classifies it as transient, so call_with_retry backs off instead of the
    agent loop crashing on `r.choices[0]`."""


@dataclass
class _ModelUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0            # prompt tokens served from the provider's cache
    billed_usd: float | None = None   # sum of provider-reported per-call cost, if any
    billed_calls: int = 0             # calls that carried a cost


@dataclass
class TokenUsage:
    """Process-wide running total of LLM token usage, broken down per model and
    with a best-effort USD cost estimate (see saage.pricing). Providers add to it
    from each response's usage field, tagged with the model id; the CLI prints it
    in the run summary and writes it to the run dir as usage.json. Token counts are
    reported by the provider (not estimated), so totals are exact when the API
    returns usage and silently 0 when it doesn't (some local servers omit it)."""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    billed_usd: float | None = None   # None until some call reports its cost
    billed_calls: int = 0
    by_model: dict[str, _ModelUsage] = field(default_factory=dict)  # model id -> usage

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def unbilled_calls(self) -> int:
        """Calls that reported no cost while others did — a partial bill."""
        return self.calls - self.billed_calls if self.billed_usd is not None else 0

    def reset(self) -> None:
        """Zero the running total — called at the start of each `saage run` so a
        process that runs more than once (resume, tests, embedding) reports this
        run's usage, not the sum since process start."""
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0
        self.billed_usd = None
        self.billed_calls = 0
        self.by_model = {}

    def add(self, usage, model: str = "?") -> None:
        if usage is None:
            return
        # OpenAI: prompt_tokens/completion_tokens; Anthropic: input_/output_tokens
        p = int(_field(usage, "prompt_tokens") or _field(usage, "input_tokens") or 0)
        c = int(_field(usage, "completion_tokens") or _field(usage, "output_tokens") or 0)
        # Provider-billed cost: OpenRouter returns `usage.cost` (USD) when the
        # request asks for it (see OpenAIProvider). It is what the account is
        # actually charged — routed provider, cache discounts and all — which a
        # list-price table cannot know. Cached prompt tokens ride along in
        # prompt_tokens_details.cached_tokens (OpenAI wire shape).
        cost = _num(_field(usage, "cost"))
        cached = int(_num(_field(_field(usage, "prompt_tokens_details"), "cached_tokens")) or 0)
        self.calls += 1
        self.prompt_tokens += p
        self.completion_tokens += c
        self.cached_tokens += cached
        mu = self.by_model.get(model)
        if mu is None:                           # don't build a throwaway each call
            mu = self.by_model[model] = _ModelUsage()
        mu.calls += 1
        mu.prompt_tokens += p
        mu.completion_tokens += c
        mu.cached_tokens += cached
        if cost is not None:
            self.billed_usd = (self.billed_usd or 0.0) + cost
            self.billed_calls += 1
            mu.billed_usd = (mu.billed_usd or 0.0) + cost
            mu.billed_calls += 1

    @property
    def billed(self) -> float | None:
        """USD the provider reported billing for this run's calls, or None if no
        call carried a cost. Preferred over `cost`: it is the real charge."""
        return self.billed_usd

    @property
    def cost(self) -> float | None:
        """Total USD estimated from SAAGE_PRICES rates across priced models, or
        None if no model's rate is configured (a cost is shown only when it's
        grounded — there is no built-in price table, see saage.pricing)."""
        from .pricing import cost as _cost
        total, priced = 0.0, False
        for model, u in self.by_model.items():
            c = _cost(model, u.prompt_tokens, u.completion_tokens)
            if c is not None:
                total += c
                priced = True
        return total if priced else None

    def as_dict(self) -> dict:
        """Serializable summary for usage.json (per-model + estimated cost)."""
        from .pricing import cost as _cost
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "billed_cost_usd": self.billed_usd,          # provider-reported (authoritative)
            "billed_calls": self.billed_calls,
            "estimated_cost_usd": self.cost,             # from SAAGE_PRICES only
            "by_model": {
                m: {"calls": u.calls, "prompt_tokens": u.prompt_tokens,
                    "completion_tokens": u.completion_tokens,
                    "cached_tokens": u.cached_tokens,
                    "billed_cost_usd": u.billed_usd, "billed_calls": u.billed_calls,
                    "estimated_cost_usd": _cost(m, u.prompt_tokens,
                                                u.completion_tokens)}
                for m, u in self.by_model.items()
            },
        }


def _field(obj, name: str):
    """Read `name` off an SDK object (pydantic model, incl. extra fields) or a
    dict; None when absent. Usage payloads arrive in both shapes."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    v = getattr(obj, name, None)
    if v is None:                                # pydantic extras may live in model_extra
        extra = getattr(obj, "model_extra", None) or {}
        v = extra.get(name) if isinstance(extra, dict) else None
    return v


def _num(v) -> float | None:
    try:
        return float(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


USAGE = TokenUsage()   # the one running total for a `saage run` process


class LLMProvider(Protocol):
    def complete(self, system: str, messages: list[dict],
                 tools: list[Tool]) -> LLMResponse: ...


def _validated_timeout(rt: "float | None") -> "float | None":
    """Validate request_timeout HERE (not only in make_provider) so direct
    construction gets the same build-time error. None = SDK default."""
    if rt is None:
        return None
    if (isinstance(rt, bool) or not isinstance(rt, (int, float))
            or not math.isfinite(rt) or rt <= 0):
        raise ValueError(f"request_timeout must be a positive number of "
                         f"seconds, got {rt!r}")
    return float(rt)


def _sdk_client_kwargs(request_timeout: "float | None", sdk) -> dict:
    """SDK constructor kwargs for a validated request_timeout.

    When set: cap read/write/pool at the budget but keep a fast 5s TCP
    connect (a bare float would make an unreachable host burn the whole
    budget per attempt — and connect errors are retryable, so x attempts),
    and take max_retries=0 so saage's call_with_retry is the ONLY retry
    layer (the SDK's silent internal retries multiply with ours — a hung
    server stalled a live run 3x timeout before our layer saw one failure).

    When None: {} — SDK defaults, including its own retries, stay in
    charge, so existing flows keep their current resilience semantics.

    NOTE the budget is PER ATTEMPT: call_with_retry still classifies
    timeouts as retryable, so a genuinely hung server can cost up to
    max_attempts x request_timeout. Size the budget for one long
    legitimate turn, not as a total deadline.

    `sdk` is the imported SDK module (openai / anthropic): the granular
    timeout is built from the SDK's own `Timeout` re-export, NOT from a
    direct httpx import — the SDKs' current majors (openai>=3,
    anthropic>=1) sit on httpx2, so classic httpx may not be installed
    at all. If the re-export is missing or its signature drifts, fall
    back to the bare budget (every phase capped, connect included)."""
    if request_timeout is None:
        return {}
    timeout = request_timeout
    timeout_cls = getattr(sdk, "Timeout", None)
    if timeout_cls is not None:
        try:
            timeout = timeout_cls(request_timeout, connect=5.0)
        except TypeError:
            timeout = request_timeout
    return {"timeout": timeout, "max_retries": 0}


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
class AnthropicProvider:
    def __init__(self, model: str, max_tokens: int = 4096,
                 retry_policy: RetryPolicy | None = None,
                 request_timeout: float | None = None):
        import anthropic  # lazy: only needed when actually used
        # timeout/retry wiring: see _sdk_client_kwargs (per-attempt budget,
        # fast connect, saage-owned retries when a timeout is configured)
        self.request_timeout = _validated_timeout(request_timeout)
        self.client = anthropic.Anthropic(
            **_sdk_client_kwargs(self.request_timeout, anthropic))
        self.model = model
        self.max_tokens = max_tokens
        self.retry_policy = retry_policy or RetryPolicy()

    def _tools(self, tools: list[Tool]) -> list[dict]:
        return [{"name": t.name, "description": t.description,
                 "input_schema": t.parameters} for t in tools]

    def _messages(self, messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                content = []
                if m["text"]:
                    content.append({"type": "text", "text": m["text"]})
                for c in m["tool_calls"]:
                    content.append({"type": "tool_use", "id": c.id,
                                    "name": c.name, "input": c.args})
                out.append({"role": "assistant", "content": content})
            else:  # tool results
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": cid, "content": o}
                    for cid, o in m["results"]]})
        return out

    def complete(self, system, messages, tools):
        r = call_with_retry(
            lambda: self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system or " ",
                tools=self._tools(tools), messages=self._messages(messages)),
            policy=self.retry_policy, what="anthropic.messages.create")
        USAGE.add(getattr(r, "usage", None), self.model)
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, b.input)
                 for b in r.content if b.type == "tool_use"]
        return LLMResponse(text, calls)


# --------------------------------------------------------------------------- #
# OpenAI-compatible: OpenAI, OpenRouter, and any local server
# (Ollama, vLLM, LM Studio, llama.cpp) — they differ only by base_url / key.
# --------------------------------------------------------------------------- #
class OpenAIProvider:
    def __init__(self, model: str, base_url: str | None = None,
                 api_key_env: str = "OPENAI_API_KEY",
                 retry_policy: RetryPolicy | None = None,
                 request_timeout: float | None = None):
        import openai  # lazy
        # timeout/retry wiring: see _sdk_client_kwargs. request_timeout
        # matters for `local` servers: a 27B on consumer hardware can
        # legitimately think for >10 min (the SDK default read as a hang).
        self.request_timeout = _validated_timeout(request_timeout)
        self.client = openai.OpenAI(
            base_url=base_url,
            **_sdk_client_kwargs(self.request_timeout, openai),
            api_key=os.environ.get(api_key_env, "not-needed"))  # local needs no real key
        self.model = model
        # Resolved wiring kept as our own attrs so callers/tests assert on saage's
        # contract, not the (unpinned) openai client's internals.
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.retry_policy = retry_policy or RetryPolicy()
        # OpenRouter reports the real per-call charge (and cached-token count)
        # in `usage` when asked; other OpenAI-compatible servers reject the
        # unknown `usage` request field, so only ask where it's understood.
        self.reports_cost = bool(base_url and "openrouter.ai" in base_url)

    def _tools(self, tools: list[Tool]) -> list[dict]:
        return [{"type": "function",
                 "function": {"name": t.name, "description": t.description,
                              "parameters": t.parameters}} for t in tools]

    def _messages(self, system: str, messages: list[dict]) -> list[dict]:
        out = [{"role": "system", "content": system or ""}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m["text"] or None}
                if m["tool_calls"]:
                    msg["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                        for c in m["tool_calls"]]
                out.append(msg)
            else:  # tool results
                for cid, o in m["results"]:
                    out.append({"role": "tool", "tool_call_id": cid, "content": o})
        return out

    def complete(self, system, messages, tools):
        def _do():
            r = self.client.chat.completions.create(
                model=self.model, messages=self._messages(system, messages),
                tools=self._tools(tools) or None,
                **({"extra_body": {"usage": {"include": True}}}
                   if self.reports_cost else {}))
            # OpenRouter/proxies sometimes return HTTP 200 with an error body
            # (choices is None/empty) instead of raising. Raise INSIDE the
            # retried call so call_with_retry backs off, rather than crashing on
            # r.choices[0] below (which killed live runs).
            if not getattr(r, "choices", None):
                raise EmptyResponseError(
                    f"no choices in response: {getattr(r, 'error', None) or r!r}")
            # A present-but-empty message (no content AND no tool calls) is the
            # same proxy failure in a different coat — stealth/ox-alpha served
            # these for ~75% of tool-bearing requests once. run_agent would
            # take "" as the agent's final answer, so retry it here instead.
            msg = r.choices[0].message
            if not (msg.content or msg.tool_calls):
                raise EmptyResponseError("empty message: no content or tool calls")
            return r
        r = call_with_retry(_do, policy=self.retry_policy,
                            what="openai.chat.completions.create")
        USAGE.add(getattr(r, "usage", None), self.model)
        m = r.choices[0].message
        calls = [ToolCall(tc.id, tc.function.name, _parse_tool_args(tc.function.arguments))
                 for tc in (m.tool_calls or [])]
        return LLMResponse(m.content or "", calls)


def _parse_tool_args(raw: str | None) -> dict:
    """Parse a tool call's arguments WITHOUT trusting the model to emit valid
    JSON — some (seen live: deepseek) occasionally produce single-quoted
    pseudo-JSON, which crashed a run at json.loads. Fall back to
    ast.literal_eval; as a last resort wrap the raw string so tool dispatch
    fails with an ERROR string the model sees and self-corrects (the same
    contract as every other tool failure)."""
    if not raw:
        return {}
    try:
        out = json.loads(raw)
        if isinstance(out, dict):
            return out
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        import ast
        out = ast.literal_eval(raw)
        if isinstance(out, dict):
            return out
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        pass
    return {"_malformed_arguments": raw}


# --------------------------------------------------------------------------- #
# Scripted: deterministic, network-free (tests)
# --------------------------------------------------------------------------- #
class ScriptedProvider:
    """Replays a fixed sequence of LLMResponses, in call order."""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.i = 0

    def complete(self, system, messages, tools):
        if self.i >= len(self.script):
            raise AssertionError(
                f"ScriptedProvider exhausted after {self.i} calls "
                f"(system starts: {(system or '')[:60]!r})")
        r = self.script[self.i]
        self.i += 1
        return r
