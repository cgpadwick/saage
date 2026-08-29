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
