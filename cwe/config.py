"""Engine configuration — currently the `run_command` safety policy.

`run_command` runs arbitrary shell (`shell=True`) with the engine's privileges, so
by default the engine refuses an obviously destructive command (recursive force
deletes, privilege escalation, raw-device writes, fork bombs, pipe-to-shell RCE,
reads of credential files, …) *before* it executes. A refused command is returned
to the model as an `ERROR:` string — non-fatal, so the agent simply learns it
cannot do that and tries another way.

This is **defense in depth, not a sandbox.** A denylist over a `shell=True` string
can always be evaded (obfuscation, env-var indirection, base64). The real isolation
boundary is still a container/VM (see the README security note); this layer stops
the casual/accidental and the obvious-attack cases.

Rules are configurable via an engine config YAML (CLI `--config engine.yaml`):

    command_policy:
      use_defaults: true          # start from the built-in denylist (default true)
      deny:                       # extra regex patterns (searched, case-insensitive)
        - '\bterraform\s+destroy\b'
      allow:                      # regex exceptions that override a deny match
        - '^rm -rf \./build\b'
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Built-in denylist. Each entry is a regex `search`ed against the command string
# (case-insensitive). Kept deliberately conservative to avoid false positives on
# ordinary ML/shell work (`python train.py`, `pytest -q`, `rm stale.txt`, `git …`).
DEFAULT_DENY: tuple[str, ...] = (
    # --- recursive/forced deletion ---
    r"\brm\s+-[a-z]*r[a-z]*f",            # rm -rf, -rfv, -Rf
    r"\brm\s+-[a-z]*f[a-z]*r",            # rm -fr
    r"\brm\b(?=.*\s-[a-z]*r)(?=.*\s-[a-z]*f)",   # rm -r … -f (separate flags)
    r"\brm\s+(-[a-z]+\s+)*--recursive\b",        # rm --recursive
    r"\brm\s+-[a-z]*r[a-z]*\s+[^|;&]*\*",        # rm -r … *  (wildcard wipe)
    # --- privilege escalation ---
    r"\bsudo\b", r"\bdoas\b", r"(^|\s)su\s+-",
    # --- fork bomb ---
    r":\s*\(\s*\)\s*\{.*[:|].*\}",
    # --- filesystem / raw device destruction ---
    r"\bmkfs(\.\w+)?\b", r"\bwipefs\b", r"\bshred\b",
    r"\bdd\b[^\n]*\bof=/dev/",
    r">\s*/dev/(sd|nvme|hd|vd|mmcblk|disk)",
    # --- power state ---
    r"\b(shutdown|reboot|halt|poweroff)\b", r"\binit\s+0\b",
    # --- credential / sensitive files ---
    r"/etc/(passwd|shadow|sudoers|gshadow)\b",
    r"(^|\s|/)\.ssh/", r"\bid_rsa\b", r"\bauthorized_keys\b",
    # --- pipe a download straight into a shell (remote code execution) ---
    r"\b(curl|wget|fetch)\b[^\n]*\|\s*(sudo\s+)?(ba|z|da|k|c)?sh\b",
    # --- reverse shells ---
    r"/dev/tcp/", r"\b(nc|ncat|netcat)\b[^\n]*\s-[a-z]*e", r"\bbash\s+-i\b",
    # --- chmod/chown the filesystem root world-writable / recursively ---
    r"\bchmod\s+(-R\s+)?0*777\s+/(\s|$)",
    r"\bchown\s+-R\b[^\n]*\s/(\s|$)",
)


@dataclass
class CommandPolicy:
    """A denylist (with optional allow-overrides) over `run_command` strings."""
    deny: list[str]
    allow: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._deny = [(p, re.compile(p, re.IGNORECASE)) for p in self.deny]
        self._allow = [re.compile(p, re.IGNORECASE) for p in self.allow]

    def check(self, command: str) -> str | None:
        """Return a denial reason if `command` is blocked, else None. An `allow`
        pattern that matches overrides a deny hit (an explicit carve-out)."""
        for pattern, rx in self._deny:
            if rx.search(command):
                if any(a.search(command) for a in self._allow):
                    return None
                return f"blocked by command policy (matched deny pattern {pattern!r})"
        return None

    @classmethod
    def default(cls) -> "CommandPolicy":
        return cls(deny=list(DEFAULT_DENY))

    @classmethod
    def unrestricted(cls) -> "CommandPolicy":
        return cls(deny=[])


@dataclass
class EngineConfig:
    command_policy: CommandPolicy

    @classmethod
    def default(cls) -> "EngineConfig":
        return cls(command_policy=CommandPolicy.default())


def load_engine_config(path: str | Path | None = None) -> EngineConfig:
    """Load an engine config YAML, or the safe built-in defaults when `path` is
    None. `command_policy.use_defaults` (default true) keeps the built-in denylist
    and *adds* any `deny:` patterns; set it false to start from an empty denylist."""
    if path is None:
        return EngineConfig.default()
    data = yaml.safe_load(Path(path).read_text()) or {}
    cp = data.get("command_policy") or {}
    deny = list(DEFAULT_DENY) if cp.get("use_defaults", True) else []
    deny += list(cp.get("deny") or [])
    allow = list(cp.get("allow") or [])
    return EngineConfig(command_policy=CommandPolicy(deny=deny, allow=allow))
