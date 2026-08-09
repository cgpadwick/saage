"""The tool-use loop. Bounded by max_steps, so it always terminates."""
from __future__ import annotations

import logging

from .llm import LLMProvider
from .spinner import Spinner
from .tools import Tool

log = logging.getLogger(__name__)


def _brief(args: dict) -> str:
    """A short, human-readable summary of a tool call's arguments."""
    for k in ("command", "path", "paths", "ref", "name", "message", "query"):
        if k in args:
            v = str(args[k]).replace("\n", " ")
            return v if len(v) <= 70 else v[:67] + "..."
    return ", ".join(map(str, args)) if args else ""


# A single tool result must never dominate the model's context window. An agent
# that reads a large data file whole (read_file) or runs a command with huge
# output (run_command) would otherwise push one conversation past the model's
# context limit — seen live: a kaggle_solver run accumulated 1.11M tokens
# against a 1M-token model and died with a 400. Cap each result to a head+tail
# slice with an explicit elision marker, so the model still sees the start
# (schema / first error) and the end (final result / traceback) and is nudged
# to read in slices instead. ~40k chars ≈ 10k tokens; 20 maxed results then sum
# to well under a 1M-token window.
MAX_TOOL_RESULT_CHARS = 40_000
_HEAD_CHARS = 30_000
_TAIL_CHARS = 8_000


def _cap(out: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    if len(out) <= limit:
        return out
    elided = len(out) - _HEAD_CHARS - _TAIL_CHARS
    return (f"{out[:_HEAD_CHARS]}\n\n... [saage truncated {elided} chars of "
            f"tool output to fit the context window — read files in slices "
            f"(head/tail/sed -n) or grep for the part you need] ...\n\n"
            f"{out[-_TAIL_CHARS:]}")


def run_agent(provider: LLMProvider, system: str, task: str,
              tools: list[Tool], max_steps: int = 20,
              max_tool_result_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
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
            results.append((call.id, _cap(out, max_tool_result_chars)))
        messages.append({"role": "tool", "results": results})
    log.warning("    agent hit max_steps=%d without finishing", max_steps)
    return last_text
