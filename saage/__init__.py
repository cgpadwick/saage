"""SAAGE — Super Awesome Agentic Graph Engine.

A tiny deterministic composable agentic workflow engine on PocketFlow.

Control flow (loops, polling, retry, exit conditions) is owned by code; the LLM
only ever chooses content. Workflows are authored in YAML and hydrated into
PocketFlow flows.
"""
from .hydrate import build_flow, run_flow

__all__ = ["build_flow", "run_flow"]
__version__ = "0.1.0"
