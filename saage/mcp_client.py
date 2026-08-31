"""MCP client: spawn the stdio MCP servers a flow declares and wrap their
tools as saage Tools.

A flow opts in with a top-level `mcp:` block (see validate_spec); each entry
names a local stdio server and the env-var NAMES it needs. Values resolve
env var → credentials.toml [mcp.<server>] (written by `saage mcp add`) —
same order as API keys, so `export`-based setups win unchanged.

The mcp SDK is async; the engine is sync. Each server gets a daemon thread
running its own asyncio loop, and ONE long-lived task owns the stdio_client/
ClientSession context managers from enter to exit (anyio cancel scopes must
enter and exit in the same task — splitting them across run_coroutine_
threadsafe calls corrupts the scope stack). Tool calls are posted into the
loop and awaited with a timeout from the calling thread.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .tools import Tool

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 30     # spawn + handshake + list_tools
CALL_TIMEOUT = 120       # one tool call (the agent loop's max_steps still bounds totals)
CLOSE_TIMEOUT = 10


class McpError(RuntimeError):
    """An MCP server could not be configured/spawned; message names the fix."""


@dataclass
class McpServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)   # required env var NAMES


def parse_mcp_block(block: dict) -> list[McpServerSpec]:
    """The `mcp:` mapping from flow.yaml → specs (validate_spec ran already)."""
    return [McpServerSpec(name=name, command=entry["command"],
                          args=list(entry.get("args") or []),
                          env=list(entry.get("env") or []))
            for name, entry in (block or {}).items()]


def resolve_env(spec: McpServerSpec) -> dict[str, str]:
    """Resolve each required env-var name: process env → credentials.toml
    [mcp.<server>]. Missing names fail here, before any process is spawned,
    with the exact command that fixes it."""
    from .settings import stored_mcp_value
    resolved, missing = {}, []
    for name in spec.env:
        val = os.environ.get(name) or stored_mcp_value(spec.name, name)
        if val:
            resolved[name] = val
        else:
            missing.append(name)
    if missing:
        raise McpError(
            f"MCP server {spec.name!r} needs {', '.join(missing)}: run\n"
            f"  saage mcp add {spec.name} {' '.join(missing)}\n"
            f"or export the variable(s) in your environment")
    return resolved


def _result_text(res) -> str:
    """CallToolResult → the string fed back to the model (like every tool)."""
    parts = [c.text for c in (res.content or [])
             if getattr(c, "text", None) is not None]
    text = "\n".join(parts)
    if not text and getattr(res, "structured_content", None):
        text = json.dumps(res.structured_content)
    if getattr(res, "is_error", False):
        return f"ERROR: {text or 'tool call failed'}"
    return text


class _ThreadFuture:
    """Tiny cross-thread future: set from the loop thread, read by the ctor."""

    def __init__(self):
        self._evt = threading.Event()
        self._val = None
        self._exc = None

    def set_result(self, val):
        self._val = val
        self._evt.set()

    def set_exception(self, exc):
        self._exc = exc
        self._evt.set()

    def result(self, timeout):
        if not self._evt.wait(timeout):
            raise TimeoutError(f"no MCP handshake within {timeout}s")
        if self._exc is not None:
            raise self._exc
        return self._val


class McpClient:
    """One running stdio MCP server + its tools wrapped as saage Tools."""

    def __init__(self, spec: McpServerSpec, env: dict[str, str] | None = None,
                 cwd: Path | None = None):
        self.spec = spec
        self._session = None
        self._close_evt: asyncio.Event | None = None
        self._handshake = _ThreadFuture()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        name=f"mcp:{spec.name}", daemon=True)
        self._thread.start()
        self._runner = asyncio.run_coroutine_threadsafe(
            self._run(env or {}, cwd), self._loop)
        try:
            raw = self._handshake.result(CONNECT_TIMEOUT)
        except Exception as e:
            self.close()
            raise McpError(
                f"MCP server {spec.name!r} ({spec.command} "
                f"{' '.join(spec.args)}) failed to start: {e}") from e
        self.tools = [self._wrap(t) for t in raw]
        log.info("mcp %s: %d tool(s): %s", spec.name, len(self.tools),
                 ", ".join(t.name for t in self.tools))

    async def _run(self, env: dict[str, str], cwd: Path | None):
        """The single task that owns every context manager, start to finish."""
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        self._close_evt = asyncio.Event()
        try:
            params = StdioServerParameters(command=self.spec.command,
                                           args=self.spec.args, env=env or None,
                                           cwd=str(cwd) if cwd else None)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self._session = session
                    self._handshake.set_result(listed.tools)
                    await self._close_evt.wait()
        except BaseException as e:
            self._handshake.set_exception(e)   # no-op once the handshake is done
            raise
        finally:
            self._session = None

    def _wrap(self, t) -> Tool:
        public = f"{self.spec.name}__{t.name}"
        schema = t.input_schema if isinstance(t.input_schema, dict) else \
            {"type": "object", "properties": {}}
        orig = t.name

        def call(**kwargs) -> str:
            return self.call(orig, kwargs)

        return Tool(name=public, description=t.description or "",
                    parameters=schema, fn=call)

    def call(self, tool_name: str, args: dict) -> str:
        if self._session is None:
            return f"ERROR: MCP server {self.spec.name!r} is not connected"
        fut = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(tool_name, args or {}), self._loop)
        try:
            return _result_text(fut.result(CALL_TIMEOUT))
        except TimeoutError:
            fut.cancel()
            return (f"ERROR: MCP tool {tool_name!r} timed out after "
                    f"{CALL_TIMEOUT}s")

    def close(self) -> None:
        """Idempotent; unwinds the runner task, then stops the loop/thread."""
        runner, self._runner = self._runner, None
        if runner is not None:
            if self._close_evt is not None:
                self._loop.call_soon_threadsafe(self._close_evt.set)
            else:
                runner.cancel()
            try:
                runner.result(CLOSE_TIMEOUT)
            except Exception:  # noqa: BLE001 — a dying server must not fail the run
                pass
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(CLOSE_TIMEOUT)
        if not self._thread.is_alive() and not self._loop.is_closed():
            self._loop.close()


def connect_servers(block: dict, cwd: Path | None = None) -> list[McpClient]:
    """Resolve secrets and start every server in a flow's `mcp:` block.
    All-or-nothing: a failure closes the ones already started."""
    clients: list[McpClient] = []
    try:
        for spec in parse_mcp_block(block):
            clients.append(McpClient(spec, resolve_env(spec), cwd=cwd))
    except Exception:
        for c in clients:
            c.close()
        raise
    return clients
