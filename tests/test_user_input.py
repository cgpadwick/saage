"""ask_user tool — reads a typed line interactively; in a non-interactive run it
returns an ERROR instead of blocking (so backgrounded/piped/CI runs never hang)."""
from saage.tools import ask_user, default_tools


def test_ask_user_returns_typed_line():
    out = ask_user("Approve the plan?",
                   _isatty=lambda: True, _input=lambda prompt: "yes please  ")
    assert out == "yes please"                       # trailing whitespace stripped


def _must_not_read(prompt):
    raise AssertionError("input() must not be called when stdin is not a TTY")


def test_ask_user_non_tty_returns_error_without_reading():
    out = ask_user("Approve?", _isatty=lambda: False, _input=_must_not_read)
    assert out.startswith("ERROR:") and "TTY" in out  # graceful, never blocked


def test_ask_user_eof_is_graceful():
    def _eof(prompt):
        raise EOFError
    out = ask_user("Q?", _isatty=lambda: True, _input=_eof)
    assert out.startswith("ERROR:") and "end-of-input" in out


def test_ask_user_in_default_tools(tmp_path):
    assert "ask_user" in {t.name for t in default_tools(tmp_path)}
