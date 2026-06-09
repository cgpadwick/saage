from cwe_testkit import call, resp

from cwe.agent import run_agent
from cwe.llm import LLMResponse, ScriptedProvider
from cwe.tools import file_tools


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
from cwe.nodes import AgentNode, render
from cwe.skills import Skill


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


def test_agentnode_templates_the_skill_body():
    prov = _CapturingProvider("done")
    node = AgentNode("t", _skill("SKILL_ID: t\nAnswer this: {{ question }}"), prov, [])
    out = node.exec(node.prep({"question": "what is 2+2?"}))
    assert out == "done"
    assert "what is 2+2?" in prov.system        # body placeholder was filled in
    assert "{{" not in prov.system              # nothing left unrendered


def test_agentnode_raw_block_preserves_literal_braces():
    prov = _CapturingProvider()
    body = "SKILL_ID: t\nEmit a literal {% raw %}{{ token }}{% endraw %} please."
    node = AgentNode("t", _skill(body), prov, [])
    node.exec(node.prep({}))
    assert "{{ token }}" in prov.system         # raw block kept the literal braces


def test_render_warns_on_undefined_but_does_not_fail(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        out = render("hello {{ missing }}!", {})
    assert out == "hello !"                      # undefined -> "" (non-fatal)
    assert caplog.records                        # ...but a warning was emitted
