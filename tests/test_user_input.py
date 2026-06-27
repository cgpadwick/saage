"""ask_user tool — reads a typed line interactively; in a non-interactive run (or
on Ctrl+C / EOF / no stdin) it returns an ERROR instead of blocking or aborting.
It is an OPT-IN tool: a skill gets it only by naming it in its tools: allow-list."""
from saage.nodes import AgentNode
from saage.skills import Skill
from saage.tools import OPT_IN_TOOL_NAMES, ask_user, default_tools, opt_in_tools


def test_ask_user_returns_typed_line():
    out = ask_user("Approve the plan?",
                   _isatty=lambda: True, _input=lambda prompt: "yes please  ")
    assert out == "yes please"                       # trailing whitespace stripped


def test_ask_user_preserves_leading_whitespace():
    # only TRAILING whitespace is stripped (rstrip) — leading can be meaningful
    out = ask_user("Indent?", _isatty=lambda: True,
                   _input=lambda prompt: "    indented\t ")
    assert out == "    indented"


def _must_not_read(prompt):
    raise AssertionError("input() must not be called when stdin is not a TTY")


def test_ask_user_non_tty_returns_error_without_reading():
    out = ask_user("Approve?", _isatty=lambda: False, _input=_must_not_read)
    assert out.startswith("ERROR:") and "TTY" in out  # graceful, never blocked


def test_ask_user_eof_is_graceful():
    def _eof(prompt):
        raise EOFError
    assert ask_user("Q?", _isatty=lambda: True, _input=_eof).startswith("ERROR:")


def test_ask_user_keyboardinterrupt_is_graceful():
    # Ctrl+C at the prompt must NOT escape as a BaseException and kill the run
    def _interrupt(prompt):
        raise KeyboardInterrupt
    out = ask_user("Q?", _isatty=lambda: True, _input=_interrupt)
    assert out.startswith("ERROR:") and "cancelled" in out


def test_ask_user_no_stdin_is_non_tty(monkeypatch):
    # sys.stdin can be None (embedded / detached) -> treat as non-interactive
    import saage.tools as tools_mod
    monkeypatch.setattr(tools_mod.sys, "stdin", None)
    out = ask_user("Q?", _input=_must_not_read)      # _isatty=None -> real path
    assert out.startswith("ERROR:") and "TTY" in out


# --- opt-in wiring: ask_user is NOT a default tool; only granted when listed ---

def test_ask_user_not_in_default_tools(tmp_path):
    assert "ask_user" not in {t.name for t in default_tools(tmp_path)}
    assert "ask_user" in OPT_IN_TOOL_NAMES


def test_opt_in_tools_builds_only_requested():
    assert [t.name for t in opt_in_tools(["ask_user", "read_file"])] == ["ask_user"]
    assert opt_in_tools(["read_file"]) == []
    assert opt_in_tools(None) == []


def _skill(tool_names):
    return Skill(name="t", description="d", system="b", dir=".", tools=tool_names)


def test_agentnode_grants_ask_user_only_when_listed(tmp_path):
    base = default_tools(tmp_path)
    # listed -> the agent gets ask_user even though it's not in `base`
    node = AgentNode("t", _skill(["read_file", "ask_user"]), None, base)
    assert "ask_user" in {t.name for t in node.tools}
    # not listed (allow-list omits it) -> no ask_user
    node2 = AgentNode("t", _skill(["read_file"]), None, base)
    assert "ask_user" not in {t.name for t in node2.tools}
    # NO allow-list (all default tools) -> still no ask_user (it's opt-in only)
    node3 = AgentNode("t", _skill([]), None, base)
    assert "ask_user" not in {t.name for t in node3.tools}
