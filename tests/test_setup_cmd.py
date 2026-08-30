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
# agent wiring (skills + MCP registration), offered at the end of the wizard
# ------------------------------------------------------------------------- #

class _Result:
    def __init__(self, rc=0, err=""):
        self.returncode, self.stdout, self.stderr = rc, "", err


def test_wire_agents_installs_skills_and_registers(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/claude")
    calls, lines = [], []
    from saage.setup import wire_agents
    wire_agents(claude_dir=tmp_path / ".claude",
                run_cmd=lambda cmd: calls.append(cmd) or _Result(0),
                out=lines.append)
    for name in ("building-saage-flows", "designing-saage-flows"):
        skill = tmp_path / ".claude" / "skills" / name / "SKILL.md"
        assert skill.is_file() and "saage" in skill.read_text()
    assert calls and calls[0][:6] == ["claude", "mcp", "add", "-s", "user", "saage"]
    assert calls[0][-1] == "mcp"
    assert any("registered" in ln for ln in lines)

    # second run: idempotent, nothing rewritten
    lines2 = []
    wire_agents(claude_dir=tmp_path / ".claude",
                run_cmd=lambda cmd: _Result(0), out=lines2.append)
    assert sum("up to date" in ln for ln in lines2) == 2


def test_wire_agents_without_claude_cli_prints_manual_cmd(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: None)
    lines = []
    from saage.setup import wire_agents
    wire_agents(claude_dir=tmp_path / ".claude",
                run_cmd=lambda cmd: (_ for _ in ()).throw(AssertionError("no CLI run")),
                out=lines.append)
    assert any("claude mcp add -s user saage" in ln for ln in lines)
    assert any("mcpServers" in ln for ln in lines)      # snippet for other clients


def test_wire_agents_registration_failure_is_not_fatal(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/claude")
    lines = []
    from saage.setup import wire_agents
    wire_agents(claude_dir=tmp_path / ".claude",
                run_cmd=lambda cmd: _Result(1, "already exists"),
                out=lines.append)
    assert any("register manually" in ln for ln in lines)


def test_wizard_offers_agent_wiring(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    wired = []
    import saage.setup as setup_mod
    monkeypatch.setattr(setup_mod, "wire_agents", lambda **kw: wired.append(1))
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
