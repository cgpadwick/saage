"""User-level defaults + API-key store, written by `saage setup`.

Two files under ~/.saage (SAAGE_HOME relocates, as everywhere):

  config.yaml        non-secret defaults — the provider spec a flow inherits
                     when its flow.yaml has no `provider:` block:
                         provider:
                           type: openrouter
                           model: "deepseek/deepseek-v4-flash"
  credentials.toml   secrets (chmod 600, shared with `saage remote`): a
                     `[keys]` section maps env-var names to API keys, e.g.
                         [keys]
                         OPENROUTER_API_KEY = "sk-or-..."

Resolution order everywhere: env var → credentials.toml [keys]. Env always
wins, so CI and existing `export`-based setups see no behavior change.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .paths import saage_home

# saage.remote.creds owns credentials.toml parsing (incl. the chmod-600
# preflight); the [keys] section just rides in the same file.
from .remote.creds import CredsError, cred_path, load_creds


def config_path() -> Path:
    return saage_home() / "config.yaml"


def load_defaults() -> dict:
    """The whole ~/.saage/config.yaml mapping ({} when absent/empty)."""
    p = config_path()
    if not p.is_file():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def default_provider() -> dict | None:
    """The saved default provider spec, or None if setup hasn't been run."""
    prov = load_defaults().get("provider")
    return dict(prov) if isinstance(prov, dict) and prov.get("type") else None


def save_default_provider(spec: dict) -> Path:
    """Write the `provider:` block of config.yaml, preserving any other
    top-level keys the file may grow later."""
    cfg = load_defaults()
    cfg["provider"] = {k: v for k, v in spec.items() if v is not None}
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def stored_key(env_var: str) -> str | None:
    """The API key saved for *env_var* in credentials.toml [keys], if any.
    A malformed/badly-permissioned credentials file yields None here — the
    run path must degrade to 'no stored key', not crash; `saage setup` and
    `saage doctor` surface the underlying problem."""
    try:
        key = load_creds().get("keys", {}).get(env_var)
    except Exception:  # noqa: BLE001 — see docstring
        return None
    return key or None


def save_key(env_var: str, value: str) -> Path:
    """Insert or replace `env_var = "..."` in the [keys] section by text
    splice (same convention as remote.creds targets: a TOML re-emit would
    strip comments and reorder the whole file). chmod 600 like every write."""
    if '"' in value or "\\" in value or "\n" in value:
        raise CredsError("API key contains characters that can't be stored "
                         '(quote/backslash/newline)')
    path = cred_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    entry = f'{env_var} = "{value}"'
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "[keys]")
    except StopIteration:                       # no [keys] section yet — append one
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["[keys]", entry]
    else:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].lstrip().startswith("[")), len(lines))
        for i in range(start + 1, end):
            if lines[i].split("=", 1)[0].strip() == env_var:
                lines[i] = entry                # replace in place
                break
        else:
            lines.insert(end, entry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path
