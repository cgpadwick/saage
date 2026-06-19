"""Credentials + target registry: ~/.saage/credentials.toml.

Layout (chmod 600, created by `saage remote init` / `add-target`):

    [storage]                      # optional — R2/S3 mirror, not required:
    endpoint = "https://..."       # with no [storage], artifacts live in the
    bucket = "saage-runs"          # node-side run dir and are read over SSH
    access_key = "..."
    secret_key = "..."

    [targets.spark]
    host = "spark.local"
    user = "saage"                 # dedicated unprivileged run user
    hourly_usd = 0.0               # optional: shown by status/ps as a reminder

Env vars override file values. SAAGE_HOME relocates ~/.saage (used by tests).
"""
from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import saage_home

try:
    import tomllib                      # 3.11+
except ModuleNotFoundError:             # 3.10
    import tomli as tomllib

# flow provider.type -> env var holding the key on the local machine. v1
# pushes that key to the node ("reuse" mode); per-run capped keys are a
# planned upgrade (docs/remote_handoff_plan.md §4.2).
PROVIDER_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "local": "LOCAL_API_KEY",  # optional for local servers
}


class CredsError(RuntimeError):
    pass


def cred_path() -> Path:
    return saage_home() / "credentials.toml"


def ssh_key_path() -> Path:
    return saage_home() / "ssh" / "saage_ed25519"


@dataclass
class Target:
    name: str
    host: str
    user: str | None = None
    port: int = 22
    hourly_usd: float = 0.0
    key: Path = field(default_factory=ssh_key_path)


@dataclass
class Storage:
    """An S3-compatible mirror (Cloudflare R2). Optional: with no [storage]
    section, artifacts live only in the node-side run dir, read over SSH."""
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "auto"

    def run_prefix(self, run_id: str) -> str:
        return f"runs/{run_id}"


def storage_config(creds: dict | None = None) -> Storage | None:
    creds = load_creds() if creds is None else creds
    s = creds.get("storage")
    if not s:
        return None
    values = [s.get(k, "") for k in ("endpoint", "bucket", "access_key", "secret_key")]
    # treat placeholders ("<paste-...>") and blanks as not-yet-configured
    if any(not v or v.startswith("<") for v in values):
        return None
    return Storage(endpoint=s["endpoint"], bucket=s["bucket"],
                   access_key=s["access_key"], secret_key=s["secret_key"],
                   region=s.get("region", "auto"))


def load_creds() -> dict:
    path = cred_path()
    creds: dict = {}
    if path.exists():
        # POSIX group/world bits are meaningless on NTFS (chmod is a no-op and
        # stat always reports 0o666); a per-user %USERPROFILE% file is already
        # ACL-protected from other users, the moral equivalent of 0600
        if os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise CredsError(
                    f"refusing {path}: permissions are {oct(mode)} "
                    f"(group/world readable); run: chmod 600 {path}"
                )
        creds = tomllib.loads(path.read_text(encoding="utf-8"))
    for section, key, env in [
        ("storage", "access_key", "SAAGE_STORAGE_ACCESS_KEY"),
        ("storage", "secret_key", "SAAGE_STORAGE_SECRET_KEY"),
    ]:
        if os.environ.get(env):
            creds.setdefault(section, {})[key] = os.environ[env]
    return creds


def _resolve_key(raw: str) -> Path:
    """A target's `key` as written, or — when that path doesn't exist on THIS
    machine — the same-named key under ~/.saage/ssh/. Credentials files travel
    between machines (a laptop pulls them from a workstation), and an absolute
    key path written on one OS is meaningless on the other."""
    p = Path(raw).expanduser()
    if p.is_file():
        return p
    # basename must parse across OSes: a Windows-written path read on Linux
    # has backslash separators that PosixPath.name doesn't split
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    alt = saage_home() / "ssh" / base
    return alt if alt.is_file() else p


def list_targets(creds: dict | None = None) -> dict[str, Target]:
    creds = load_creds() if creds is None else creds
    out: dict[str, Target] = {}
    for name, t in creds.get("targets", {}).items():
        out[name] = Target(
            name=name,
            host=t["host"],
            user=t.get("user"),
            port=int(t.get("port", 22)),
            hourly_usd=float(t.get("hourly_usd", 0.0)),
            key=_resolve_key(t["key"]) if t.get("key") else ssh_key_path(),
        )
    return out


def get_target(name: str) -> Target:
    targets = list_targets()
    if name not in targets:
        known = ", ".join(sorted(targets)) or "(none registered)"
        raise CredsError(
            f"unknown target {name!r} — known targets: {known}. "
            f"Register one with: saage remote add-target {name} --host <host>"
        )
    return targets[name]


def add_target(name: str, host: str, user: str | None = None, port: int = 22,
               hourly_usd: float | None = None, key: str | None = None) -> Path:
    """Append a [targets.<name>] section. Errors if the target already exists."""
    if any(c in name for c in " .[]\"'"):
        raise CredsError(f"invalid target name {name!r}")
    if key and "'" in key:
        # the key path is written as a TOML literal string; a quote inside
        # would corrupt the whole credentials file for every target
        raise CredsError(f"key path may not contain a single quote: {key!r}")
    path = cred_path()
    if path.exists() and name in list_targets():
        raise CredsError(f"target {name!r} already exists in {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"\n[targets.{name}]", f'host = "{host}"']
    if user:
        lines.append(f'user = "{user}"')
    if port != 22:
        lines.append(f"port = {port}")
    if hourly_usd is not None:
        lines.append(f"hourly_usd = {hourly_usd}")
    if key:
        # TOML literal string: backslashes in a Windows path must not be
        # parsed as escape sequences
        lines.append(f"key = '{key}'")   # per-instance keys (e.g. Thunder Compute)
    existing = path.read_text() if path.exists() else ""
    path.write_text(existing + "\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def ensure_ssh_key() -> Path:
    """Generate the dedicated saage keypair if missing; return the private key path."""
    key = ssh_key_path()
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", "saage-remote", "-q"],
            check=True,
        )
    return key
