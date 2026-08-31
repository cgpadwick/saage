"""The `mcp:` flow block end to end: validate schema, hydrate wiring, the
per-skill opt-in gate, and the `saage mcp add/list/rm` secret commands.
Offline — the only server ever spawned is tests/fake_mcp_server.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from saage.hydrate import build_flow
from saage.validate import FlowSpecError, validate_spec

FAKE = str(Path(__file__).resolve().parent / "fake_mcp_server.py")


def _spec(mcp):
    return {"mcp": mcp,
            "workflow": [{"id": "s", "type": "command", "run": "echo hi"}]}


def _err(mcp) -> str:
    with pytest.raises(FlowSpecError) as e:
        validate_spec(_spec(mcp), require_provider=False)
    return str(e.value)


# --- schema -----------------------------------------------------------------

def test_mcp_must_be_mapping():
    assert "'mcp:' must be a mapping" in _err(["not", "a", "map"])


def test_mcp_server_needs_command():
    assert "needs 'command'" in _err({"reddit": {"args": ["x"]}})


def test_mcp_unknown_key_flagged():
    assert "unknown key 'cmd'" in _err({"reddit": {"cmd": "uvx"}})


def test_mcp_env_must_be_name_list():
    msg = _err({"reddit": {"command": "uvx", "env": {"KEY": "value"}}})
    assert "list of env-var NAMES" in msg


def test_mcp_bad_server_name():
    assert "server name" in _err({"bad name!": {"command": "uvx"}})


def test_valid_mcp_block_passes():
    validate_spec(_spec({"reddit": {"command": "uvx", "args": ["reddit-mcp"],
                                    "env": ["REDDIT_CLIENT_ID"]}}),
                  require_provider=False)


# --- hydrate wiring ---------------------------------------------------------

def _write_flow(tmp_path, skill_tools_line):
    (tmp_path / "use_echo").mkdir()
    (tmp_path / "use_echo" / "skill.md").write_text(
        f"---\nname: use_echo\ndescription: use the echo tool\n"
        f"{skill_tools_line}---\nSKILL_ID: use_echo\nCall the tool.\n",
        encoding="utf-8")
    f = tmp_path / "flow.yaml"
    f.write_text(
        "mcp:\n"
        "  fake:\n"
        f"    command: {sys.executable}\n"
        f"    args: [{FAKE!r}, '--profile', 'echo']\n"
        "workflow:\n"
        "  - { id: go, type: agent, skill: use_echo }\n", encoding="utf-8")
    return f


def test_allowlisted_skill_gets_namespaced_mcp_tool(tmp_path):
    f = _write_flow(tmp_path, "tools: [fake__echo, read_file]\n")
    flow, _ = build_flow(f, provider=object(), workspace=str(tmp_path / "ws"))
    try:
        node = flow.start_node
        names = {t.name for t in node.tools}
        assert names == {"fake__echo", "read_file"}
        echo = next(t for t in node.tools if t.name == "fake__echo")
        assert echo.run(text="hi") == "echo: hi"
    finally:
        for c in flow.mcp_clients:
            c.close()


def test_skill_without_allowlist_never_sees_mcp_tools(tmp_path):
    f = _write_flow(tmp_path, "")          # no tools: line -> default set only
    flow, _ = build_flow(f, provider=object(), workspace=str(tmp_path / "ws"))
    try:
        names = {t.name for t in flow.start_node.tools}
        assert not any(n.startswith("fake__") for n in names)
        assert "read_file" in names        # default set intact
    finally:
        for c in flow.mcp_clients:
            c.close()


def test_connect_mcp_false_skips_spawn_and_secrets(tmp_path):
    # server command doesn't exist AND env secrets are missing — a
    # validate-only build must still succeed
    (tmp_path / "use_echo").mkdir()
    (tmp_path / "use_echo" / "skill.md").write_text(
        "---\nname: use_echo\ndescription: d\n---\nbody\n", encoding="utf-8")
    f = tmp_path / "flow.yaml"
    f.write_text(
        "mcp:\n"
        "  ghost: { command: saage-no-such-binary-xyz, env: [NO_SUCH_VAR] }\n"
        "workflow:\n"
        "  - { id: go, type: agent, skill: use_echo }\n", encoding="utf-8")
    flow, _ = build_flow(f, provider=object(), workspace=str(tmp_path / "ws"),
                         connect_mcp=False)
    assert flow.mcp_clients == []


# --- CLI secret registry ----------------------------------------------------

def test_cli_mcp_add_list_rm(monkeypatch, capsys):
    import getpass

    from saage.cli import _main
    vals = iter(["cid-123", "cs-456"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(vals))
    assert _main(["mcp", "add", "reddit",
                  "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"]) == 0
    assert _main(["mcp", "list"]) == 0
    out = capsys.readouterr().out
    assert "reddit: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET" in out
    assert "cid-123" not in out                      # values never printed
    assert _main(["mcp", "rm", "reddit"]) == 0
    assert _main(["mcp", "rm", "reddit"]) == 1       # already gone


def test_cli_mcp_add_rejects_empty_value(monkeypatch):
    import getpass

    from saage.cli import _main
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "")
    assert _main(["mcp", "add", "reddit", "REDDIT_CLIENT_ID"]) == 1
