"""saage.mcp_client against the deterministic fake server (offline, no key).

The fake (tests/fake_mcp_server.py) is a REAL stdio MCP server run as a
subprocess of these tests — so connect/handshake/list/call/close exercise the
actual SDK plumbing, threads and all, with canned content.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from saage.mcp_client import (McpClient, McpError, McpServerSpec,
                              connect_servers, parse_mcp_block, resolve_env)

FAKE = str(Path(__file__).resolve().parent / "fake_mcp_server.py")


def _spec(profile="echo", name="fake"):
    return McpServerSpec(name=name, command=sys.executable,
                         args=[FAKE, "--profile", profile])


@pytest.fixture(scope="module")
def client():
    c = McpClient(_spec())
    yield c
    c.close()


def test_tools_are_namespaced_by_server_name(client):
    assert {t.name for t in client.tools} == {"fake__echo", "fake__env_probe",
                                              "fake__boom"}


def test_call_roundtrip(client):
    echo = next(t for t in client.tools if t.name == "fake__echo")
    assert echo.run(text="hello") == "echo: hello"


def test_server_side_exception_comes_back_as_error_string(client):
    boom = next(t for t in client.tools if t.name == "fake__boom")
    out = boom.run()
    # the SDK masks the exception text server-side; the shape is what matters
    assert out.startswith("ERROR:") and "boom" in out


def test_env_reaches_the_server_process():
    c = McpClient(_spec(), env={"MCP_TEST_SECRET": "s3cr3t"})
    try:
        probe = next(t for t in c.tools if t.name == "fake__env_probe")
        assert probe.run(name="MCP_TEST_SECRET") == "s3cr3t"
    finally:
        c.close()


def test_close_is_idempotent():
    c = McpClient(_spec())
    c.close()
    c.close()


def test_unspawnable_command_raises_mcp_error():
    bad = McpServerSpec(name="nope", command="saage-no-such-binary-xyz")
    with pytest.raises(McpError, match="failed to start"):
        McpClient(bad)


def test_connect_servers_closes_started_on_failure(monkeypatch):
    # first server fine, second unspawnable -> the first must be closed
    block = {"fake": {"command": sys.executable,
                      "args": [FAKE, "--profile", "echo"]},
             "nope": {"command": "saage-no-such-binary-xyz"}}
    with pytest.raises(McpError):
        connect_servers(block)


# --- env resolution ---------------------------------------------------------

def test_resolve_env_prefers_process_env(monkeypatch):
    monkeypatch.setenv("MY_VAR", "from-env")
    monkeypatch.setattr("saage.settings.stored_mcp_value",
                        lambda s, v: "from-creds")
    spec = McpServerSpec(name="x", command="c", env=["MY_VAR"])
    assert resolve_env(spec) == {"MY_VAR": "from-env"}


def test_resolve_env_falls_back_to_credentials(monkeypatch):
    monkeypatch.delenv("MY_VAR", raising=False)
    monkeypatch.setattr("saage.settings.stored_mcp_value",
                        lambda s, v: "from-creds" if v == "MY_VAR" else None)
    spec = McpServerSpec(name="x", command="c", env=["MY_VAR"])
    assert resolve_env(spec) == {"MY_VAR": "from-creds"}


def test_resolve_env_missing_names_the_fix(monkeypatch):
    monkeypatch.delenv("MY_VAR", raising=False)
    monkeypatch.setattr("saage.settings.stored_mcp_value", lambda s, v: None)
    spec = McpServerSpec(name="reddit", command="c", env=["MY_VAR"])
    with pytest.raises(McpError, match="saage mcp add reddit MY_VAR"):
        resolve_env(spec)


def test_parse_mcp_block_defaults():
    specs = parse_mcp_block({"r": {"command": "uvx"}})
    assert specs[0].name == "r" and specs[0].args == [] and specs[0].env == []
