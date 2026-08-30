"""`saage setup` — interactive first-run configuration, aws-configure style.

Asks for a default provider, a default model, and (for hosted providers) an
API key; validates the key with a cheap live call; saves the defaults to
~/.saage/config.yaml and the key to ~/.saage/credentials.toml [keys]
(chmod 600). Flows without a `provider:` block then run with these defaults;
flows that pin one still win (see saage.hydrate.build_flow).

TTY-only: the wizard never runs under a pipe/CI — it errors with the export
alternative instead of hanging on stdin. Tests inject `input_fn`/`getpass_fn`/
`check_fn`, the same convention as tools.ask_user.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .llm import PROVIDER_ENV
from .settings import config_path, save_default_provider, save_key, stored_key

# provider -> (suggested model, note). Suggestions only — any id is accepted.
_PROVIDERS = [
    ("openrouter", "deepseek/deepseek-v4-flash", "one key, many models"),
    ("anthropic", "claude-opus-4-8", "Anthropic Messages API"),
    ("openai", "gpt-4o", "api.openai.com"),
    ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b", "NVIDIA NIM"),
    ("local", "llama3.1:8b", "Ollama/vLLM/LM Studio — no key"),
]
_BASE_URLS = {"openrouter": "https://openrouter.ai/api/v1",
              "nvidia": "https://integrate.api.nvidia.com/v1"}
_DEFAULT_LOCAL_URL = "http://localhost:11434/v1"


def _check_key(ptype: str, key: str, base_url: str | None) -> None:
    """One cheap authenticated call so a mistyped key fails here, not three
    steps into a flow. Raises on failure."""
    if ptype == "anthropic":
        import anthropic
        anthropic.Anthropic(api_key=key).models.list()
    else:
        import openai
        openai.OpenAI(base_url=base_url or _BASE_URLS.get(ptype),
                      api_key=key).models.list(timeout=15)


def _ask(input_fn, prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    got = input_fn(f"{prompt}{suffix}: ").strip()
    return got or default


def _saage_bin() -> str:
    """Absolute path to the saage executable in this environment. MCP clients
    spawn the server themselves, usually without this venv on PATH — a bare
    'saage' in their config would not resolve."""
    exe = Path(sys.executable).with_name("saage.exe" if os.name == "nt" else "saage")
    return str(exe) if exe.exists() else "saage"


def wire_agents(claude_dir=None, run_cmd=None, out=print) -> None:
    """Wire up coding agents (the Graft pattern): install the flow-design and
    flow-authoring skills where Claude Code auto-discovers them in every
    project (~/.claude/skills), register the `saage mcp` server with the
    claude CLI when it is on PATH, and print the config line for every other
    MCP client. Idempotent; called from the setup wizard."""
    import shutil
    import subprocess

    def _run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    run_cmd = run_cmd or _run

    assets = Path(__file__).parent / "agent_assets"
    skills_dir = Path(claude_dir or Path.home() / ".claude") / "skills"
    for src in sorted(p for p in assets.iterdir() if (p / "SKILL.md").is_file()):
        skill = (src / "SKILL.md").read_bytes()
        dest = skills_dir / src.name / "SKILL.md"
        if dest.exists() and dest.read_bytes() == skill:
            out(f"  skill {src.name}: up to date ({dest.parent})")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(skill)
        out(f"  skill {src.name}: installed → {dest.parent}")

    bin_ = _saage_bin()
    manual = f"claude mcp add -s user saage {bin_} mcp"
    if shutil.which("claude"):
        try:
            r = run_cmd(["claude", "mcp", "add", "-s", "user", "saage", bin_, "mcp"])
            if r.returncode == 0:
                out("  Claude Code: MCP server registered (scope: user)")
            else:
                tail = (r.stderr or r.stdout or "").strip().splitlines()
                out(f"  Claude Code: not registered ({tail[-1] if tail else 'nonzero exit'})")
                out(f"    register manually: {manual}")
        except Exception as e:  # noqa: BLE001 — wiring must never sink the wizard
            out(f"  Claude Code: could not run the claude CLI ({e})")
            out(f"    register manually: {manual}")
    else:
        out(f"  Claude Code: CLI not on PATH — once installed, run: {manual}")
    out(f"  other MCP clients (Cursor, Codex, Windsurf, …): command `{bin_}`, args `[\"mcp\"]`")
    out(f'    Cursor ~/.cursor/mcp.json: {{"mcpServers": {{"saage": '
        f'{{"command": "{bin_}", "args": ["mcp"]}}}}}}')


def run_setup(input_fn=None, getpass_fn=None, check_fn=_check_key) -> int:
    """The wizard. Returns a process exit code."""
    if input_fn is None:
        if not sys.stdin.isatty():
            print("saage: error: `saage setup` is interactive and stdin is not "
                  "a terminal — run it in a terminal, or export the provider "
                  "key (e.g. OPENROUTER_API_KEY) instead", file=sys.stderr)
            return 1
        input_fn = input
    if getpass_fn is None:
        import getpass
        getpass_fn = getpass.getpass
    try:
        return _wizard(input_fn, getpass_fn, check_fn)
    except (EOFError, KeyboardInterrupt):
        # Ctrl-C / Ctrl-D at any prompt is a cancel, not a crash — and nothing
        # is written until the very end, so a cancel really saves nothing
        print("\nsetup cancelled — nothing saved", file=sys.stderr)
        return 1


def _wizard(input_fn, getpass_fn, check_fn) -> int:
    from .settings import default_provider
    current = default_provider() or {}
    print("saage setup — choose the default provider flows run with when their")
    print("flow.yaml doesn't pin one. A flow's own `provider:` block still wins.\n")
    for i, (name, model, note) in enumerate(_PROVIDERS, 1):
        marker = " (current)" if name == current.get("type") else ""
        print(f"  {i}) {name:<11} {note}{marker}")

    names = [p[0] for p in _PROVIDERS]
    while True:
        raw = _ask(input_fn, "\nprovider (name or number)",
                   current.get("type") or "openrouter")
        ptype = names[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(names) \
            else raw.lower()
        if ptype in names:
            break
        print(f"  unknown provider {raw!r} — pick one of: {', '.join(names)}")

    suggested = dict((n, m) for n, m, _ in _PROVIDERS)[ptype]
    if current.get("type") == ptype and current.get("model"):
        suggested = current["model"]
    model = _ask(input_fn, "model", suggested)

    base_url = None
    if ptype == "local":
        base_url = _ask(input_fn, "base_url",
                        current.get("base_url") or _DEFAULT_LOCAL_URL)

    key = None
    key_env = PROVIDER_ENV.get(ptype)
    if key_env:
        have = "env" if os.environ.get(key_env) else \
               "saved" if stored_key(key_env) else None
        prompt = f"API key ({key_env})" + \
                 (f" [keep existing ({have})]" if have else "")
        key = getpass_fn(f"{prompt}: ").strip()
        if not key and not have:
            print(f"saage: error: {ptype} needs an API key", file=sys.stderr)
            return 1
        if key:
            try:
                check_fn(ptype, key, base_url)
                print("  key check: ok")
            except Exception as e:  # noqa: BLE001 — show it, let the user decide
                print(f"  key check FAILED: {e}")
                if _ask(input_fn, "save it anyway? (y/N)", "n").lower() != "y":
                    print("nothing saved")
                    return 1

    spec = {"type": ptype, "model": model}
    if base_url:
        spec["base_url"] = base_url
    cfg = save_default_provider(spec)
    print(f"\nsaved defaults to {cfg}: {ptype} / {model}")
    if key:
        cred = save_key(key_env, key)
        print(f"saved {key_env} to {cred} (chmod 600)")

    if _ask(input_fn, "\nwire up coding agents? installs the flow-design/"
            "authoring skills for Claude Code and registers the `saage mcp` "
            "server (y/N)", "n").lower().startswith("y"):
        wire_agents()

    print("try it:  saage run flows/story_writer/flow.yaml")
    return 0
