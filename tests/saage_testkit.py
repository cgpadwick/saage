"""Deterministic, network-free test doubles for the LLM provider."""
from __future__ import annotations

import re

from saage.llm import LLMResponse, ToolCall


def resp(text: str = "", calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(text, calls or [])


def call(name: str, **args) -> ToolCall:
    return ToolCall(id="t", name=name, args=args)


def tool_turn(name: str, **args) -> list[LLMResponse]:
    """One agent invocation that makes a single tool call then finishes."""
    return [resp(calls=[call(name, **args)]), resp("done")]


class RoutedProvider:
    """Routes each call to a per-skill queue, keyed by a `SKILL_ID: <name>`
    marker in the skill body (the `system` prompt). Robust to loops/interleaving:
    each skill's responses are consumed in order across all its invocations.
    """

    def __init__(self, scripts: dict[str, list[LLMResponse]]):
        self.queues = {k: list(v) for k, v in scripts.items()}
        self.calls: list[str] = []

    def complete(self, system, messages, tools):
        m = re.search(r"SKILL_ID:\s*(\w+)", system or "")
        skill_id = m.group(1) if m else "?"
        queue = self.queues.get(skill_id)
        if not queue:
            raise AssertionError(
                f"RoutedProvider: no scripted response left for skill {skill_id!r}")
        self.calls.append(skill_id)
        return queue.pop(0)
