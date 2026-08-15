"""Flow discovery: scan configured dirs for */flow.yaml, hydrate each for free
validation, and cache name/description/knobs/spec for the API, the NL parser's
prompt, and the DAG builder. Broken flows are listed with their error — an
invisible flow is a debugging trap."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..hydrate import build_flow

log = logging.getLogger(__name__)


@dataclass
class FlowInfo:
    name: str
    path: Path
    description: str
    knobs: dict
    spec: dict
    error: str | None = None


def _description(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()
        if line.strip():
            return ""
    return ""


class FlowCatalog:
    def __init__(self, config):
        self.config = config
        self.flows: dict[str, FlowInfo] = {}

    def refresh(self) -> None:
        found: dict[str, FlowInfo] = {}
        for base in self.config.flow_paths:
            for fy in sorted(Path(base).glob("*/flow.yaml")):
                name = fy.parent.name
                if name in found:
                    log.warning("catalog: duplicate flow %r at %s ignored", name, fy)
                    continue
                found[name] = self._load(name, fy)
        self.flows = found

    def get(self, name: str):
        return self.flows.get(name)

    def _load(self, name: str, fy: Path) -> FlowInfo:
        text = fy.read_text()
        spec = yaml.safe_load(text) or {}
        knobs = {k: str(v) for k, v in (spec.get("shared") or {}).items()}
        info = FlowInfo(name, fy, _description(text), knobs, spec)
        try:
            # hydrate against a throwaway workspace: free schema validation
            build_flow(fy, provider=object(), workspace=fy.parent)
        except Exception as e:                                # noqa: BLE001
            info.error = str(e)
        return info
