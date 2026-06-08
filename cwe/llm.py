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
import os
from dataclasses import dataclass, field
from typing import Protocol

from .retry import RetryPolicy, call_with_retry
from .tools import Tool


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(Protocol):
    def complete(self, system: str, messages: list[dict],
                 tools: list[Tool]) -> LLMResponse: ...


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
class AnthropicProvider:
    def __init__(self, model: str, max_tokens: int = 4096,
                 retry_policy: RetryPolicy | None = None):
        import anthropic  # lazy: only needed when actually used
        self.client = anthropic.Anthropic()
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
                 retry_policy: RetryPolicy | None = None):
        import openai  # lazy
        self.client = openai.OpenAI(
            base_url=base_url,
            api_key=os.environ.get(api_key_env, "not-needed"))  # local needs no real key
        self.model = model
        self.retry_policy = retry_policy or RetryPolicy()

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
        r = call_with_retry(
            lambda: self.client.chat.completions.create(
                model=self.model, messages=self._messages(system, messages),
                tools=self._tools(tools) or None),
            policy=self.retry_policy, what="openai.chat.completions.create")
        m = r.choices[0].message
        calls = [ToolCall(tc.id, tc.function.name, json.loads(tc.function.arguments))
                 for tc in (m.tool_calls or [])]
        return LLMResponse(m.content or "", calls)


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
