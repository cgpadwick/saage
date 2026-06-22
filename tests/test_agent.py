import logging

import pytest

from saage_testkit import call, resp

from saage.agent import run_agent
from saage.llm import LLMResponse, ScriptedProvider
from saage.nodes import AgentNode, render
from saage.skills import Skill
from saage.tools import file_tools


def test_loop_executes_tool_then_finishes(tmp_path):
    tools = file_tools(tmp_path)
    provider = ScriptedProvider([
        resp(calls=[call("write_file", path="out.txt", content="hi")]),
        resp("all done"),
    ])
    result = run_agent(provider, "sys", "write a file", tools)
    assert result == "all done"
    assert (tmp_path / "out.txt").read_text() == "hi"


def test_unknown_tool_is_reported_not_raised(tmp_path):
    # tool errors are surfaced back to the model, not raised
    provider = ScriptedProvider([
        resp(calls=[call("nope")]),
        resp("recovered"),
    ])
    assert run_agent(provider, "sys", "task", file_tools(tmp_path)) == "recovered"


def test_max_steps_bounds_the_loop(tmp_path):
    # a provider that always calls a tool must still terminate
    always = ScriptedProvider([
        resp(calls=[call("read_file", path="missing")]) for _ in range(50)
    ])
    out = run_agent(always, "sys", "task", file_tools(tmp_path), max_steps=3)
    assert always.i == 3            # stopped at the bound
    assert out == ""


# --------------------------------------------------------------------------- #
# AgentNode templates the skill *body* (not just the description) — so
# instructions like "answer {{ question }}" get filled from the shared store
# before the model sees them. (regression: bodies used to be passed raw.)
# --------------------------------------------------------------------------- #
class _CapturingProvider:
    """Records the system prompt it was handed, then returns a fixed reply."""

    def __init__(self, reply="ok"):
        self.system = None
        self.reply = reply

    def complete(self, system, messages, tools):
        self.system = system
        return resp(self.reply)


def _skill(body, description="do the thing"):
    return Skill(name="t", description=description, system=body, dir=".", tools=[])


# --------------------------------------------------------------------------- #
# AgentNode must not SILENTLY drop unknown tool names from a skill's allow-list
# --------------------------------------------------------------------------- #
def _skill_tools(names):
    return Skill(name="t", description="d", system="b", dir=".", tools=names)


def test_agentnode_warns_and_filters_unknown_tool(tmp_path, caplog):
    available = file_tools(tmp_path)             # real tools incl. read_file
    with caplog.at_level(logging.WARNING):
        node = AgentNode("t", _skill_tools(["read_file", "bogus_tool"]), None, available)
    names = {t.name for t in node.tools}
    assert "read_file" in names and "bogus_tool" not in names   # known kept, unknown dropped
    assert "bogus_tool" in caplog.text                          # but the drop is WARNED, not silent


def test_agentnode_all_unknown_tools_raises(tmp_path):
    # if the allow-list intersects nothing, the agent would run tool-less — hard error
    with pytest.raises(ValueError, match="unknown tool"):
        AgentNode("t", _skill_tools(["bogus", "nope"]), None, file_tools(tmp_path))


def test_agentnode_empty_tools_list_means_all_tools(tmp_path):
    # tools: [] (falsy) is the established "no allow-list -> all tools" case; unchanged
    available = file_tools(tmp_path)
    node = AgentNode("t", _skill_tools([]), None, available)
    assert {t.name for t in node.tools} == {t.name for t in available}


def test_agentnode_templates_the_skill_body():
    prov = _CapturingProvider("done")
    node = AgentNode("t", _skill("SKILL_ID: t\nAnswer this: {{ question }}"), prov, [])
    out = node.exec(node.prep({"question": "what is 2+2?"}))
    assert out == "done"
    assert "what is 2+2?" in prov.system        # body placeholder was filled in
    assert "{{" not in prov.system              # nothing left unrendered


def test_agentnode_raw_block_preserves_literal_braces():
    prov = _CapturingProvider()
    body = ("SKILL_ID: t\nFor {{ name }}, emit a literal "
            "{% raw %}{{ token }}{% endraw %} please.")
    node = AgentNode("t", _skill(body), prov, [])
    node.exec(node.prep({"name": "Ada"}))
    assert "{{ token }}" in prov.system         # raw block kept the literal braces
    assert "For Ada," in prov.system            # ...but vars outside raw still render


def test_render_warns_on_undefined_but_does_not_fail(caplog):
    with caplog.at_level(logging.WARNING):
        out = render("hello {{ missing }}!", {})
    assert out == "hello !"                      # undefined -> "" (non-fatal)
    assert caplog.records                        # ...but a warning was emitted


def test_render_default_filter_does_not_warn(caplog):
    # a var guarded by `| default(...)` is the supported "maybe-absent" pattern
    # (greenfield uses it) and must NOT emit an undefined warning.
    with caplog.at_level(logging.WARNING):
        out = render('{{ proposal | default("(none)") }}', {})
    assert out == "(none)"
    assert not caplog.records                    # silent — no spurious warning


def test_malformed_tool_args_never_crash_and_surface_to_model():
    """Models sometimes emit single-quoted pseudo-JSON tool arguments (seen
    live: killed an 18-experiment run at json.loads). The parse must degrade:
    valid JSON -> dict; python-literal dict -> dict; garbage -> a wrapper that
    makes tool dispatch return an ERROR string the model can react to."""
    from saage.llm import _parse_tool_args

    assert _parse_tool_args('{"path": "a.txt"}') == {"path": "a.txt"}
    assert _parse_tool_args("{'path': 'a.txt'}") == {"path": "a.txt"}   # ast fallback
    assert _parse_tool_args("") == {}
    assert _parse_tool_args(None) == {}
    out = _parse_tool_args("not a dict at all {")
    assert out == {"_malformed_arguments": "not a dict at all {"}
    assert _parse_tool_args('"just a string"') == {"_malformed_arguments": '"just a string"'}


def test_token_usage_accumulates_from_provider():
    """USAGE sums provider-reported tokens; CLI prints it. Exact (not
    estimated): providers report usage, so a run finally answers 'how many
    tokens did this cost' (was silently discarded before)."""
    from types import SimpleNamespace
    from saage.llm import TokenUsage

    u = TokenUsage()
    u.add(SimpleNamespace(prompt_tokens=100, completion_tokens=20))   # OpenAI shape
    u.add(SimpleNamespace(input_tokens=50, output_tokens=10))         # Anthropic shape
    u.add(None)                                                       # missing usage: ignored
    assert u.calls == 2
    assert u.prompt_tokens == 150
    assert u.completion_tokens == 30
    assert u.total_tokens == 180
