"""Wire coding agents up to saage (the `saage setup` "wire up coding agents"
step): detect which agents are installed, let the user pick, then CONFIGURE
them — write each agent's own MCP config and install the flow skills — rather
than printing snippets to paste.

Every agent's config location derives from `home` (injectable for tests, and
never touched for agents the user didn't select). JSON configs are merged —
only the `saage` server entry is added/replaced, everything else in the file
is preserved byte-for-byte semantically (re-serialized, comments aside: these
files are machine-managed JSON). Codex's TOML is spliced textually so the rest
of the file, comments included, is untouched.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def saage_bin() -> str:
    """Absolute path to the saage executable in this environment. MCP clients
    spawn the server themselves, usually without this venv on PATH — a bare
    'saage' in their config would not resolve."""
    exe = Path(sys.executable).with_name("saage.exe" if os.name == "nt" else "saage")
    return str(exe) if exe.exists() else "saage"


def _merge_mcp_json(path: Path, bin_: str) -> str:
    """Add/replace the `saage` entry under `mcpServers` in a JSON config,
    preserving everything else. Returns a short status for the ✓ line."""
    cfg: dict = {}
    if path.is_file():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{path} is not valid JSON ({e}) — not touching it")
    existing = cfg.setdefault("mcpServers", {}).get("saage")
    entry = {"command": bin_, "args": ["mcp"]}
    if existing == entry:
        return f"already configured ({path})"
    cfg["mcpServers"]["saage"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return f"MCP server {'updated' if existing else 'added'} in {path}"


def _splice_codex_toml(path: Path, bin_: str) -> str:
    """Add/replace `[mcp_servers.saage]` in Codex's config.toml by text splice
    (same convention as remote.creds: a TOML re-emit would strip comments).
    The command path is a TOML *literal* string: a Windows path in a basic
    string ("C:\\Users\\...") is a backslash-escape soup that corrupts the
    whole file. Literal strings can't hold a single quote — same guard as
    remote.creds key paths."""
    if "'" in bin_:
        raise RuntimeError(f"saage path contains a single quote ({bin_!r}) — "
                           f"can't write it to config.toml; add the server "
                           f"manually")
    section = (f"[mcp_servers.saage]\ncommand = '{bin_}'\nargs = [\"mcp\"]\n")
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(section, encoding="utf-8")
        return f"MCP server added in {path}"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    try:
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip() == "[mcp_servers.saage]")
    except StopIteration:
        body = "".join(lines)
        sep = "" if body.endswith("\n\n") else "\n" if body.endswith("\n") else "\n\n"
        path.write_text(body + sep + section, encoding="utf-8")
        return f"MCP server added in {path}"
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("[")), len(lines))
    path.write_text("".join(lines[:start]) + section + "".join(lines[end:]),
                    encoding="utf-8")
    return f"MCP server updated in {path}"


def _install_skills(claude_dir: Path, out_lines: list[str]) -> None:
    assets = Path(__file__).parent / "agent_assets"
    fresh = 0
    for src in sorted(p for p in assets.iterdir() if (p / "SKILL.md").is_file()):
        skill = (src / "SKILL.md").read_bytes()
        dest = claude_dir / "skills" / src.name / "SKILL.md"
        if dest.exists() and dest.read_bytes() == skill:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(skill)
        fresh += 1
    if fresh:
        out_lines.append(f"{fresh} skill(s) installed to {claude_dir / 'skills'} "
                         f"(restart any open Claude Code session to load them)")
    else:
        out_lines.append(f"skills up to date ({claude_dir / 'skills'})")


@dataclass
class Agent:
    key: str
    label: str
    note: str                                  # what configuring touches
    detect: Callable[[Path], bool]
    configure: Callable[[Path, str, Callable], list[str]]


def _claude_configure(home: Path, bin_: str, run_cmd) -> list[str]:
    lines: list[str] = []
    _install_skills(home / ".claude", lines)
    if shutil.which("claude"):
        # anything short of a clean exit — nonzero, a hang past the timeout,
        # a spawn failure — falls back to the direct ~/.claude.json write, so
        # the skills already installed above are never reported as a failure
        try:
            r = run_cmd(["claude", "mcp", "add", "-s", "user", "saage", bin_, "mcp"])
            if r.returncode == 0:
                lines.append("MCP server registered via the claude CLI (scope: user)")
                return lines
            why = ((r.stderr or r.stdout or "").strip().splitlines() or
                   ["nonzero exit"])[-1]
        except Exception as e:  # noqa: BLE001 — e.g. TimeoutExpired
            why = str(e)
        lines.append(f"claude CLI registration failed ({why}) — writing "
                     f"~/.claude.json directly")
    # no CLI (or it failed): user-scope MCP servers live in ~/.claude.json
    lines.append(_merge_mcp_json(home / ".claude.json", bin_))
    return lines


def _json_agent(relpath: str):
    def configure(home: Path, bin_: str, run_cmd) -> list[str]:
        return [_merge_mcp_json(home / relpath, bin_)]
    return configure


AGENTS = [
    Agent("claude-code", "Claude Code", "skills + MCP (claude CLI / ~/.claude.json)",
          lambda home: bool(shutil.which("claude")) or (home / ".claude").is_dir(),
          _claude_configure),
    Agent("cursor", "Cursor", "~/.cursor/mcp.json",
          lambda home: (home / ".cursor").is_dir(),
          _json_agent(".cursor/mcp.json")),
    Agent("codex", "Codex", "~/.codex/config.toml",
          lambda home: bool(shutil.which("codex")) or (home / ".codex").is_dir(),
          lambda home, bin_, run_cmd: [_splice_codex_toml(home / ".codex" / "config.toml", bin_)]),
    Agent("windsurf", "Windsurf", "~/.codeium/windsurf/mcp_config.json",
          lambda home: (home / ".codeium" / "windsurf").is_dir(),
          _json_agent(".codeium/windsurf/mcp_config.json")),
    Agent("gemini", "Gemini CLI", "~/.gemini/settings.json",
          lambda home: bool(shutil.which("gemini")) or (home / ".gemini").is_dir(),
          _json_agent(".gemini/settings.json")),
]


def _parse_selection(raw: str, detected: list[str]) -> list[str]:
    """'1 3', 'cursor codex', 'all', 'none', '' (= the detected ones)."""
    raw = raw.strip().lower()
    if raw in ("", "detected"):
        return detected
    if raw == "all":
        return [a.key for a in AGENTS]
    if raw in ("none", "n", "skip"):
        return []
    picked: list[str] = []
    for tok in raw.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= len(AGENTS):
            picked.append(AGENTS[int(tok) - 1].key)
            continue
        match = [a.key for a in AGENTS if tok in a.key or tok in a.label.lower()]
        if len(match) == 1:
            picked.append(match[0])
        else:
            raise ValueError(tok)
    return list(dict.fromkeys(picked))          # dedupe, keep order


def wire_agents(input_fn=input, run_cmd=None, home: Path | None = None,
                out=print) -> None:
    """The interactive picker: list agents (detected ones marked), take a
    selection, configure each. A single agent failing never aborts the rest.
    All prompts go through `input_fn`; tests inject it plus `home`/`run_cmd`."""
    import subprocess

    def _run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    run_cmd = run_cmd or _run
    home = Path(home) if home else Path.home()

    detected = [a.key for a in AGENTS if a.detect(home)]
    out("")
    for i, a in enumerate(AGENTS, 1):
        mark = "detected" if a.key in detected else "-"
        out(f"  {i}) {a.label:<11} {mark:<9} {a.note}")
    default = "detected: " + ", ".join(detected) if detected else "none"
    while True:
        raw = input_fn(f"configure which? (numbers/names, 'all', 'none') "
                       f"[{default}]: ").strip()
        try:
            picked = _parse_selection(raw, detected)
            break
        except ValueError as tok:
            out(f"  don't know {tok} — pick from the list above")
    if not picked:
        out("  skipped — re-run `saage setup` anytime to wire agents")
        return

    bin_ = saage_bin()
    for key in picked:
        agent = next(a for a in AGENTS if a.key == key)
        try:
            for line in agent.configure(home, bin_, run_cmd):
                out(f"  ✓ {agent.label}: {line}")
        except Exception as e:  # noqa: BLE001 — report, keep wiring the rest
            out(f"  ✗ {agent.label}: {e}")
    if "claude-code" not in picked:
        out("  note: the flow-authoring skills install with Claude Code; other "
            "agents read AGENTS.md in the repo instead")
