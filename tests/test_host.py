"""Host mode (saage.host): the driving coding agent answers each agent step
through a file mailbox; the engine keeps all control flow. A thread plays the
host here, so this stays offline and deterministic."""
import threading
import time

import pytest

from saage import host
from saage.host import HostProvider, HostTimeout, read_pending, submit
from saage.hydrate import make_provider, run_flow


def _fake_host(mailbox, answers: dict[str, list[str]], seen: list, stop):
    """Answer each posted step from a per-skill queue, like RoutedProvider."""
    while not stop.is_set():
        p = read_pending(mailbox)
        if p is None:
            time.sleep(0.01)
            continue
        seen.append(p)
        submit(mailbox, p["seq"], answers[p["skill"]].pop(0))


def _hosted(answers):
    stop, seen = threading.Event(), []
    def start(mailbox):
        t = threading.Thread(target=_fake_host, args=(mailbox, answers, seen, stop),
                             daemon=True)
        t.start()
        return t
    return start, stop, seen


def test_guessing_game_hosted(flow_copy, tmp_path, monkeypatch):
    monkeypatch.setattr(host, "_POLL", 0.01)
    flow_yaml = flow_copy("guessing_game")
    mailbox = tmp_path / "host"
    start, stop, seen = _hosted({"propose": ["0.5", "0.75", "0.62"],
                                 "judge": ["higher", "lower", "correct"]})
    start(mailbox)
    try:
        shared = run_flow(flow_yaml, provider=HostProvider(mailbox, timeout=10))
    finally:
        stop.set()

    # the engine, not the host, ran the loop, the command step, and exit_when
    assert shared["_trace"] == ["propose", "judge", "record"] * 3
    assert shared["_exit_reason"]["search"] == "exit_when"
    assert shared["guess"] == 0.62
    history = (flow_yaml.parent / "history.txt").read_text().strip().splitlines()
    assert history == ["guess=0.5 -> higher", "guess=0.75 -> lower",
                       "guess=0.62 -> correct"]

    # the host got whole, rendered steps: templated task + skill body + tools
    assert [p["step_id"] for p in seen] == ["propose", "judge"] * 3
    assert [p["seq"] for p in seen] == [1, 2, 3, 4, 5, 6]
    judge = seen[1]
    assert "The player's latest\nguess is 0.5" in judge["task"]
    assert "SKILL_ID: judge" in judge["system"]
    assert seen[0]["tools"] == ["read_file"]          # skill allow-list passed on
    assert seen[0]["workspace"] == str(flow_yaml.parent.resolve())  # where history.txt is
    assert read_pending(mailbox) is None              # nothing left outstanding


def test_unanswered_step_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "_POLL", 0.01)
    hp = HostProvider(tmp_path, timeout=0.05)
    with pytest.raises(HostTimeout, match="'s1'"):
        hp.run_step("s1", "sk", "sys", "do it", [])
    assert read_pending(tmp_path) is None             # pending cleared, not stale


def test_submit_rejects_stale_or_duplicate_seq(tmp_path):
    with pytest.raises(ValueError, match="not pending"):
        submit(tmp_path, 1, "x")                      # nothing posted yet
    host._write_json(tmp_path / "pending.json",
                     {"seq": 2, "step_id": "a", "skill": "s", "system": "",
                      "task": "", "tools": []})
    with pytest.raises(ValueError, match="outstanding: 2"):
        submit(tmp_path, 1, "x")                      # stale seq
    submit(tmp_path, 2, "ok")
    assert read_pending(tmp_path) is None             # answered → not re-handed out
    with pytest.raises(ValueError):
        submit(tmp_path, 2, "again")                  # duplicate


def test_seq_continues_past_existing_results(tmp_path):
    # a resumed run reuses the mailbox: new steps must not collide with old ones
    (tmp_path / "result-4.json").write_text('{"seq": 4, "output": "old"}')
    assert HostProvider(tmp_path)._seq == 4


def test_make_provider_host(tmp_path, monkeypatch):
    monkeypatch.delenv(host.MAILBOX_ENV, raising=False)
    with pytest.raises(ValueError, match="mailbox"):
        make_provider({"type": "host"})
    monkeypatch.setenv(host.MAILBOX_ENV, str(tmp_path / "mb"))
    hp = make_provider({"type": "host", "timeout": 5})   # no model, no key
    assert isinstance(hp, HostProvider) and hp.timeout == 5
    assert hp.mailbox == tmp_path / "mb"


def test_remote_handoff_refuses_host(tmp_path):
    from saage.remote.handoff import HandoffError, handoff
    f = tmp_path / "flow.yaml"
    f.write_text("provider: {type: host}\n"
                 "workflow:\n  - {id: a, type: command, run: 'true'}\n")
    with pytest.raises(HandoffError, match="can't be handed off"):
        handoff(flow=str(f), target=None)


@pytest.mark.skipif(__import__("os").name != "posix",
                    reason="job control needs POSIX process groups")
def test_host_cli_cycle(tmp_path, capsys, monkeypatch):
    # `saage host start/next/submit/status` against a real hosted subprocess;
    # this test plays the agent, keyed off the exit-code protocol
    import io
    import json
    import shutil
    from pathlib import Path

    from saage.cli import _main
    from saage.host_cli import EXIT_DONE, EXIT_ERROR, EXIT_STEP

    src = Path(__file__).resolve().parents[1] / "flows" / "guessing_game"
    flow = tmp_path / "guessing_game"
    shutil.copytree(src, flow)

    assert _main(["host", "start", str(flow / "flow.yaml"), "--set", "target=0.3"]) == 0
    job_id = capsys.readouterr().out.strip()

    answers = {"propose": ["0.5", "0.25", "0.3"],
               "judge": ["lower", "higher", "correct"]}
    steps = []
    for _ in range(10):                           # bounded: 6 steps + done
        rc = _main(["host", "next", "--json", "--timeout", "30"])   # latest run
        out = capsys.readouterr().out
        if rc == EXIT_DONE:
            break
        assert rc == EXIT_STEP, out
        step = json.loads(out)
        steps.append(step["step_id"])
        if step["step_id"] == "judge":            # the engine rendered the knob
            assert "hidden target is 0.3." in step["task"]
        monkeypatch.setattr("sys.stdin", io.StringIO(answers[step["skill"]].pop(0) + "\n"))
        assert _main(["host", "submit", job_id, str(step["seq"])]) == 0
    assert json.loads(out) == {"done": True, "status": "completed"}
    assert steps == ["propose", "judge"] * 3
    assert (flow / "history.txt").read_text().splitlines() == [
        "guess=0.5 -> lower", "guess=0.25 -> higher", "guess=0.3 -> correct"]

    monkeypatch.setattr("sys.stdin", io.StringIO("x"))
    assert _main(["host", "submit", "1"]) == EXIT_ERROR        # nothing pending
    assert _main(["host", "status", job_id[:12]]) == 0         # prefix resolves
    assert "completed" in capsys.readouterr().out


def test_host_cli_no_run(capsys):
    from saage.cli import _main
    assert _main(["host", "next"]) == 1
    assert "no hosted run found" in capsys.readouterr().err
