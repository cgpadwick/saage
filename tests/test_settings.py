"""User defaults (~/.saage/config.yaml) + key store (credentials.toml [keys]),
and their wiring into provider resolution. Offline; SAAGE_HOME is a tmp dir
via the autouse fixture, so nothing touches the real ~/.saage."""
import os
import stat

import pytest

from saage.hydrate import build_flow, make_provider
from saage.llm import ProviderKeyError
from saage.settings import (config_path, default_provider, save_default_provider,
                            save_key, stored_key)


# ------------------------------------------------------------------------- #
# storage round-trips
# ------------------------------------------------------------------------- #

def test_defaults_round_trip():
    assert default_provider() is None
    save_default_provider({"type": "openrouter", "model": "some/model"})
    assert default_provider() == {"type": "openrouter", "model": "some/model"}
    # re-save replaces, and drops None values
    save_default_provider({"type": "local", "model": "m", "base_url": None})
    assert default_provider() == {"type": "local", "model": "m"}


def test_save_key_creates_600_and_replaces():
    p = save_key("OPENROUTER_API_KEY", "sk-one")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stored_key("OPENROUTER_API_KEY") == "sk-one"
    save_key("OPENROUTER_API_KEY", "sk-two")          # replace, not append
    assert stored_key("OPENROUTER_API_KEY") == "sk-two"
    assert p.read_text().count("OPENROUTER_API_KEY") == 1
    save_key("ANTHROPIC_API_KEY", "sk-a")             # second var, same section
    assert stored_key("ANTHROPIC_API_KEY") == "sk-a"
    assert stored_key("OPENROUTER_API_KEY") == "sk-two"


def test_save_key_coexists_with_remote_sections():
    # [keys] rides in the same credentials.toml as remote targets — a save
    # must not clobber them, and a target add must not clobber keys
    from saage.remote.creds import add_target, list_targets
    add_target("box", host="h.example")
    save_key("OPENAI_API_KEY", "sk-x")
    assert "box" in list_targets()
    assert stored_key("OPENAI_API_KEY") == "sk-x"


def test_save_key_rejects_toml_breaking_values():
    from saage.remote.creds import CredsError
    with pytest.raises(CredsError):
        save_key("K", 'ha"ha')


@pytest.mark.skipif(os.name != "posix",
                    reason="the perms preflight is meaningless on NTFS")
def test_stored_key_degrades_to_none_on_bad_file():
    p = save_key("OPENROUTER_API_KEY", "sk-one")
    p.chmod(0o644)                       # world-readable → load_creds refuses
    assert stored_key("OPENROUTER_API_KEY") is None


# ------------------------------------------------------------------------- #
# make_provider: file-stored key fills in when the env var is unset
# ------------------------------------------------------------------------- #

def test_make_provider_uses_stored_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save_key("OPENROUTER_API_KEY", "sk-stored")
    p = make_provider({"type": "openrouter", "model": "m"})
    assert p.api_key_env == "OPENROUTER_API_KEY"
    # the key is exported so provider SDKs (which read env) see it
    assert os.environ["OPENROUTER_API_KEY"] == "sk-stored"
    os.environ.pop("OPENROUTER_API_KEY", None)        # don't leak across tests


def test_make_provider_env_wins_over_stored(monkeypatch):
    save_key("OPENROUTER_API_KEY", "sk-stored")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    make_provider({"type": "openrouter", "model": "m"})
    assert os.environ["OPENROUTER_API_KEY"] == "sk-env"


def test_missing_key_error_mentions_setup(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderKeyError, match="saage setup"):
        make_provider({"type": "openrouter", "model": "m"})


# ------------------------------------------------------------------------- #
# build_flow: flow pin → user defaults → CLI overrides on top
# ------------------------------------------------------------------------- #

def _agent_flow(tmp_path, provider_line: str = ""):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "skill.md").write_text("---\n---\nSKILL_ID: s\n")
    flow = tmp_path / "flow.yaml"
    flow.write_text(provider_line +
                    "workflow:\n  - { id: a, type: agent, skill: s }\n")
    return flow


def test_unpinned_flow_uses_setup_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_API_KEY", "unused")
    save_default_provider({"type": "local", "model": "default-model"})
    flow = _agent_flow(tmp_path)
    _, _ = build_flow(flow, workspace=str(tmp_path / "ws"))


def test_unpinned_flow_without_defaults_points_at_setup(tmp_path):
    flow = _agent_flow(tmp_path)
    with pytest.raises(ProviderKeyError, match="saage setup"):
        build_flow(flow, workspace=str(tmp_path / "ws"))


def test_flow_pin_wins_over_defaults(tmp_path, monkeypatch):
    save_default_provider({"type": "local", "model": "default-model"})
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    flow = _agent_flow(tmp_path, "provider: { type: openrouter, model: pinned }\n")
    # the pin (openrouter, no key anywhere) is used — NOT the keyless default
    with pytest.raises(ProviderKeyError, match="OPENROUTER_API_KEY"):
        build_flow(flow, workspace=str(tmp_path / "ws"))


def test_cli_override_wins_over_defaults(tmp_path):
    save_default_provider({"type": "openrouter", "model": "default-model"})
    flow = _agent_flow(tmp_path)
    _, _ = build_flow(flow, workspace=str(tmp_path / "ws"),
                      provider_overrides={"type": "local", "model": "cli-model"})


def test_command_only_flow_needs_no_provider_at_all(tmp_path):
    # no pin, no defaults, no keys — a flow with no agent steps still builds
    flow = tmp_path / "flow.yaml"
    flow.write_text("workflow:\n  - { id: say, type: command, run: 'echo hi' }\n")
    build_flow(flow, workspace=str(tmp_path / "ws"))


def test_config_path_honors_saage_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_HOME", str(tmp_path / "custom"))
    assert config_path() == tmp_path / "custom" / "config.yaml"
