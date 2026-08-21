"""Server-side config: ~/.saage/server.yaml (flow search paths, parser LLM,
bind address). Kept separate from engine config — the engine never reads this."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..paths import saage_home

log = logging.getLogger(__name__)


@dataclass
class ServerConfig:
    flow_paths: list[Path] = field(default_factory=list)
    parser_provider: dict | None = None
    host: str = "127.0.0.1"
    port: int = 8321
    source: Path | None = None   # the config file actually read; None = not found


def load_server_config(path: Path | None = None) -> ServerConfig:
    p = Path(path) if path else saage_home() / "server.yaml"
    if not p.is_file():
        return ServerConfig()
    # NOTE: relative flow_paths resolve against the server's cwd (below), so a
    # config written for one launch directory is silently empty from another —
    # serve() logs the resolved paths at startup to make that visible.
    raw = yaml.safe_load(p.read_text()) or {}
    port = 8321
    try:
        port = int(raw.get("port", 8321))
    except (ValueError, TypeError):
        log.warning("malformed port in server config (not an int), using default 8321")
    return ServerConfig(
        flow_paths=[Path(x).expanduser().resolve() for x in raw.get("flow_paths", [])],
        parser_provider=raw.get("parser_provider"),
        host=raw.get("host", "127.0.0.1"),
        port=port,
        source=p.resolve())
