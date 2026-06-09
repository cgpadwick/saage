"""The tool-use loop. Bounded by max_steps, so it always terminates."""
from __future__ import annotations

import logging

from .llm import LLMProvider
from .spinner import Spinner
from .tools import Tool

log = logging.getLogger(__name__)


def _brief(args: dict) -> str:
    """A short, human-readable summary of a tool call's arguments."""
    for k in ("command", "path", "paths", "ref", "name", "message"):
        if k in args:
            v = str(args[k]).replace("\n", " ")
            return v if len(v) <= 70 else v[:67] + "..."
    return ", ".join(map(str, args)) if args else ""


def run_agent(provider: LLMProvider, system: str, task: str,
              tools: list[Tool], max_steps: int = 20) -> str:
    by_name = {t.name: t for t in tools}
    messages: list[dict] = [{"role": "user", "text": task}]
    last_text = ""
    for _ in range(max_steps):
        log.debug("    · model call")
        with Spinner():                           # animated only on a real TTY
            resp = provider.complete(system, messages, tools)
        last_text = resp.text
        messages.append({"role": "assistant", "text": resp.text,
                         "tool_calls": resp.tool_calls})
        if not resp.tool_calls:          # model produced a final answer
            return last_text
        results = []
        for call in resp.tool_calls:
            log.info("    ⚙ %s %s", call.name, _brief(call.args))
            tool = by_name.get(call.name)
            if tool is None:
                out = f"ERROR: unknown tool {call.name!r}"
            else:
                try:
                    out = tool.run(**call.args)
                except Exception as e:   # surface the error back to the model
                    out = f"ERROR: {type(e).__name__}: {e}"
            log.debug("      → %s", out.replace("\n", " ")[:200])
            results.append((call.id, out))
        messages.append({"role": "tool", "results": results})
    log.warning("    agent hit max_steps=%d without finishing", max_steps)
    return last_text
