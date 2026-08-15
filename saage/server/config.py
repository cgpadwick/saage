"""Server-side config: ~/.saage/server.yaml (flow search paths, parser LLM,
bind address). Kept separate from engine config — the engine never reads this."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..paths import saage_home


@dataclass
class ServerConfig:
    flow_paths: list = field(default_factory=list)
    parser_provider: dict | None = None
    host: str = "127.0.0.1"
    port: int = 8321


def load_server_config(path: Path | None = None) -> ServerConfig:
    p = Path(path) if path else saage_home() / "server.yaml"
    if not p.is_file():
        return ServerConfig()
    raw = yaml.safe_load(p.read_text()) or {}
    return ServerConfig(
        flow_paths=[Path(x).expanduser().resolve() for x in raw.get("flow_paths", [])],
        parser_provider=raw.get("parser_provider"),
        host=raw.get("host", "127.0.0.1"),
        port=int(raw.get("port", 8321)))
