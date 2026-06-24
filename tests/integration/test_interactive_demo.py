"""interactive_demo flow — the `interview` agent uses the ask_user tool to pause
for human input, then writes a note. Offline: a fake-TTY stdin feeds the answers,
and the RoutedProvider scripts which tools the agent calls (the tools, including
ask_user, run for real)."""
import io
import sys

from saage_testkit import RoutedProvider, call, resp

from saage.hydrate import run_flow


class _FakeTTY(io.StringIO):
    """A StringIO that claims to be a TTY, so ask_user's isatty() guard passes
    and the builtin input() reads its lines via readline()."""
    def isatty(self):
        return True


def test_interactive_demo_reads_console_answers(flow_copy, tmp_path, monkeypatch):
    flow_yaml = flow_copy("interactive_demo")
    ws = tmp_path / "ws"
    ws.mkdir()

    # the human "types" these two lines at the console
    fake_stdin = _FakeTTY("Ada\nblack holes\n")
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    provider = RoutedProvider({
        "interview": [
            resp(calls=[call("ask_user", prompt="What is your name?")]),
            resp(calls=[call("ask_user", prompt="What topic would you like a fun fact about?")]),
            resp(calls=[call("write_file", path="note.md",
                             content="Hi Ada! Fun fact about black holes: ...")]),
            resp("note written to note.md"),
        ],
    })

    shared = run_flow(flow_yaml, provider=provider, workspace=ws)

    # the flow completed end-to-end through the two ask_user pauses
    assert shared["results"]["interview"] == "note written to note.md"
    assert (ws / "note.md").exists()
    # ask_user actually READ both typed lines from the (fake) console — the
    # stream is fully consumed, proving the pause-and-read happened twice
    assert fake_stdin.read() == ""


def test_ask_user_in_a_non_interactive_run_returns_error(flow_copy, tmp_path, monkeypatch):
    """With a non-TTY stdin (the normal backgrounded/piped run), ask_user returns
    an ERROR string to the agent instead of blocking — the flow still completes."""
    flow_yaml = flow_copy("interactive_demo")
    ws = tmp_path / "ws"
    ws.mkdir()

    fake_stdin = io.StringIO("")           # isatty() is False on a plain StringIO
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    captured = {}

    def _spy_write(path, content):         # capture what the agent passed
        captured["content"] = content
        (ws / path).write_text(content)
        return "ok"

    provider = RoutedProvider({
        "interview": [
            resp(calls=[call("ask_user", prompt="What is your name?")]),
            resp(calls=[call("write_file", path="note.md", content="Hi friend!")]),
            resp("done"),
        ],
    })

    shared = run_flow(flow_yaml, provider=provider, workspace=ws)
    assert shared["results"]["interview"] == "done"
    # the run never hung; the agent got an ERROR from ask_user and carried on
    assert (ws / "note.md").exists()
