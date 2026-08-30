"""`saage setup` wizard — scripted stdin/getpass, offline (key checks are
injected). SAAGE_HOME is a tmp dir via the autouse fixture."""
import stat

from saage.cli import main
from saage.settings import config_path, default_provider, stored_key
from saage.setup import run_setup


def _io(answers, secrets):
    """(input_fn, getpass_fn) replaying canned answers, tolerant of extras."""
    a, s = iter(answers), iter(secrets)
    return (lambda prompt="": next(a, "")), (lambda prompt="": next(s, ""))


def _ok_check(ptype, key, base_url):
    return None


def test_full_setup_writes_both_files(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    inp, gp = _io(["1", ""], ["sk-or-test"])       # provider 1, default model
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 0
    assert default_provider() == {"type": "openrouter",
                                  "model": "deepseek/deepseek-v4-flash"}
    assert stored_key("OPENROUTER_API_KEY") == "sk-or-test"
    cred = config_path().parent / "credentials.toml"
    assert stat.S_IMODE(cred.stat().st_mode) == 0o600


def test_provider_by_name_and_custom_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    inp, gp = _io(["anthropic", "my-model"], ["sk-ant-x"])
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 0
    assert default_provider() == {"type": "anthropic", "model": "my-model"}


def test_local_provider_needs_no_key():
    inp, gp = _io(["local", "llama3.1:8b", "http://box:8000/v1"], [])
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 0
    assert default_provider() == {"type": "local", "model": "llama3.1:8b",
                                  "base_url": "http://box:8000/v1"}


def test_failed_key_check_aborts_without_saving(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def bad_check(ptype, key, base_url):
        raise RuntimeError("401 nope")

    inp, gp = _io(["openai", "", "n"], ["sk-bad"])   # decline "save anyway?"
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=bad_check) == 1
    assert default_provider() is None
    assert stored_key("OPENAI_API_KEY") is None


def test_failed_key_check_can_be_overridden(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def bad_check(ptype, key, base_url):
        raise RuntimeError("proxy in the way")

    inp, gp = _io(["openai", "", "y"], ["sk-real"])  # accept "save anyway?"
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=bad_check) == 0
    assert stored_key("OPENAI_API_KEY") == "sk-real"


def test_blank_key_keeps_existing_stored_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from saage.settings import save_key
    save_key("OPENROUTER_API_KEY", "sk-keep-me")
    inp, gp = _io(["openrouter", ""], [""])          # blank at the key prompt
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 0
    assert stored_key("OPENROUTER_API_KEY") == "sk-keep-me"


def test_blank_key_with_nothing_existing_errors(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    inp, gp = _io(["openrouter", ""], [""])
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 1
    assert default_provider() is None


def test_rerun_offers_current_as_defaults(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    inp, gp = _io(["openrouter", "model-a"], ["sk-1"])
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 0
    # all-blank answers on a re-run keep provider, model, and key
    inp, gp = _io(["", ""], [""])
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 0
    assert default_provider() == {"type": "openrouter", "model": "model-a"}
    assert stored_key("OPENROUTER_API_KEY") == "sk-1"


def test_non_tty_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False})())
    assert main(["setup"]) == 1
    assert "not a terminal" in capsys.readouterr().err


def test_ctrl_d_cancels_cleanly(capsys):
    # EOF (Ctrl-D) at any prompt is a cancel, not an EOFError traceback
    def eof_input(prompt=""):
        raise EOFError
    rc = run_setup(input_fn=eof_input, getpass_fn=lambda p="": "")
    assert rc == 1
    assert "cancelled" in capsys.readouterr().err
    assert default_provider() is None


def test_ctrl_c_cancels_cleanly(capsys):
    def interrupt_getpass(prompt=""):
        raise KeyboardInterrupt
    inp, _ = _io(["openrouter", ""], [])
    rc = run_setup(input_fn=inp, getpass_fn=interrupt_getpass)
    assert rc == 1
    assert "cancelled" in capsys.readouterr().err
    assert default_provider() is None


# ------------------------------------------------------------------------- #
# agent wiring: detect -> pick -> configure (saage.agents), offered by the wizard
# ------------------------------------------------------------------------- #

import json

from saage.agents import wire_agents


class _Result:
    def __init__(self, rc=0, err=""):
        self.returncode, self.stdout, self.stderr = rc, "", err


def _inp(answers):
    a = iter(answers)
    return lambda prompt="": next(a, "")


def test_picker_detects_and_configures_selected(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: None)   # no CLIs anywhere
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".codex").mkdir()
    lines = []
    wire_agents(input_fn=_inp(["cursor codex"]), run_cmd=lambda c: _Result(0),
                home=tmp_path, out=lines.append)
    cfg = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cfg["mcpServers"]["saage"]["args"] == ["mcp"]
    toml = (tmp_path / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.saage]" in toml and 'args = ["mcp"]' in toml
    assert sum(ln.lstrip().startswith("\u2713") for ln in lines) == 2
    # detection markers shown: cursor+codex detected, others not
    assert any("Cursor" in ln and "detected" in ln for ln in lines)
    assert any("Windsurf" in ln and "detected" not in ln for ln in lines)


def test_picker_blank_answer_means_detected(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: None)
    (tmp_path / ".cursor").mkdir()
    wire_agents(input_fn=_inp([""]), run_cmd=lambda c: _Result(0),
                home=tmp_path, out=lambda s: None)
    assert (tmp_path / ".cursor" / "mcp.json").is_file()
    assert not (tmp_path / ".codex").exists()              # undetected untouched


def test_picker_none_skips_everything(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: None)
    (tmp_path / ".cursor").mkdir()
    lines = []
    wire_agents(input_fn=_inp(["none"]), run_cmd=lambda c: _Result(0),
                home=tmp_path, out=lines.append)
    assert not (tmp_path / ".cursor" / "mcp.json").exists()
    assert any("skipped" in ln for ln in lines)


def test_picker_bad_token_reprompts(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: None)
    lines = []
    wire_agents(input_fn=_inp(["frobnicator", "none"]),
                run_cmd=lambda c: _Result(0), home=tmp_path, out=lines.append)
    assert any("don't know" in ln for ln in lines)


def test_json_merge_preserves_other_servers(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: None)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(json.dumps(
        {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    wire_agents(input_fn=_inp(["cursor"]), run_cmd=lambda c: _Result(0),
                home=tmp_path, out=lambda s: None)
    cfg = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cfg["mcpServers"]["other"] == {"command": "x"}  # untouched
    assert cfg["theme"] == "dark"
    assert "saage" in cfg["mcpServers"]


def test_codex_toml_section_replaced_not_duplicated(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: None)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        "# my config\nmodel = \"o4\"\n\n[mcp_servers.saage]\ncommand = \"stale\"\n"
        "\n[mcp_servers.other]\ncommand = \"keep\"\n")
    wire_agents(input_fn=_inp(["codex"]), run_cmd=lambda c: _Result(0),
                home=tmp_path, out=lambda s: None)
    toml = (tmp_path / ".codex" / "config.toml").read_text()
    assert toml.count("[mcp_servers.saage]") == 1
    assert "stale" not in toml and "# my config" in toml
    assert "[mcp_servers.other]" in toml and "keep" in toml


def test_claude_uses_cli_and_installs_skills(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which",
                        lambda c: "/usr/bin/claude" if c == "claude" else None)
    calls, lines = [], []
    wire_agents(input_fn=_inp(["claude"]),
                run_cmd=lambda cmd: calls.append(cmd) or _Result(0),
                home=tmp_path, out=lines.append)
    for name in ("building-saage-flows", "designing-saage-flows"):
        assert (tmp_path / ".claude" / "skills" / name / "SKILL.md").is_file()
    assert calls[0][:6] == ["claude", "mcp", "add", "-s", "user", "saage"]
    assert any("registered" in ln for ln in lines)


def test_claude_cli_failure_falls_back_to_claude_json(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which",
                        lambda c: "/usr/bin/claude" if c == "claude" else None)
    lines = []
    wire_agents(input_fn=_inp(["claude"]), run_cmd=lambda c: _Result(1, "boom"),
                home=tmp_path, out=lines.append)
    cfg = json.loads((tmp_path / ".claude.json").read_text())
    assert cfg["mcpServers"]["saage"]["args"] == ["mcp"]


def test_one_agent_failing_does_not_stop_the_rest(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: None)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text("{not json")   # corrupt
    (tmp_path / ".codex").mkdir()
    lines = []
    wire_agents(input_fn=_inp(["cursor codex"]), run_cmd=lambda c: _Result(0),
                home=tmp_path, out=lines.append)
    assert any(ln.lstrip().startswith("\u2717") and "Cursor" in ln for ln in lines)
    assert "[mcp_servers.saage]" in (tmp_path / ".codex" / "config.toml").read_text()
    assert (tmp_path / ".cursor" / "mcp.json").read_text() == "{not json"


def test_wizard_offers_agent_wiring(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    wired = []
    import saage.agents as agents_mod
    monkeypatch.setattr(agents_mod, "wire_agents", lambda **kw: wired.append(1))
    inp, gp = _io(["1", "", "y"], ["sk-x"])          # 'y' at the agent prompt
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 0
    assert wired == [1]
    # declined (or blank = default n): not called
    wired.clear()
    inp, gp = _io(["1", "", "n"], ["sk-x"])
    assert run_setup(input_fn=inp, getpass_fn=gp, check_fn=_ok_check) == 0
    assert wired == []


def test_packaged_skills_match_repo_canon():
    # the wizard installs saage/agent_assets/*; the repo-discovered canon lives
    # in .claude/skills/*. They must stay byte-identical.
    from pathlib import Path
    import saage
    assets = Path(saage.__file__).parent / "agent_assets"
    canon = Path(saage.__file__).parent.parent / ".claude" / "skills"
    names = sorted(p.name for p in assets.iterdir())
    assert names == sorted(p.name for p in canon.iterdir())
    for n in names:
        assert (assets / n / "SKILL.md").read_bytes() == \
               (canon / n / "SKILL.md").read_bytes(), f"{n} drifted — re-sync"
