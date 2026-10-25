"""Host mode (saage.host): the driving coding agent answers each agent step
through a file mailbox; the engine keeps all control flow. A thread plays the
host here, so this stays offline and deterministic."""
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from saage import host
from saage.host import (HostProvider, HostTimeout, mailbox_dir, read_pending,
                        resolve_run, run_status, submit, wait_next)
from saage.hydrate import make_provider, run_flow

posix_only = pytest.mark.skipif(os.name != "posix",
                                reason="job control needs POSIX process groups")
GG = Path(__file__).resolve().parents[1] / "flows" / "guessing_game"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(host, "_POLL", 0.01)


def _run_dir(root: Path, name="r1", status="running", beat=True) -> Path:
    """A fake run dir: checkpoint status + (optionally) a live engine's beat."""
    d = root / name
    mailbox_dir(d).mkdir(parents=True)
    (d / "checkpoint.json").write_text(json.dumps({"status": status}))
    if beat:
        (mailbox_dir(d) / "heartbeat").touch()
    return d


def _age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


def _post(run_dir: Path, seq: int) -> None:
    host._write_json(mailbox_dir(run_dir) / "pending.json",
                     {"seq": seq, "step_id": "a", "skill": "s", "system": "",
                      "task": "", "tools": [], "workspace": None})


def _fake_host(run_dir, answers: dict[str, list[str]], seen: list, stop):
    """Answer each posted step from a per-skill queue, like RoutedProvider."""
    while not stop.is_set():
        p = read_pending(mailbox_dir(run_dir))
        if p is None:
            time.sleep(0.01)
            continue
        seen.append(p)
        submit(run_dir, p["seq"], answers[p["skill"]].pop(0))


def test_guessing_game_hosted(flow_copy, tmp_path):
    flow_yaml = flow_copy("guessing_game")
    run_dir = tmp_path / "run"
    hp = HostProvider(mailbox_dir(run_dir), timeout=10)   # starts the heartbeat
    stop, seen = threading.Event(), []
    answers = {"propose": ["0.5", "0.75", "0.62"], "judge": ["higher", "lower", "correct"]}
    threading.Thread(target=_fake_host, args=(run_dir, answers, seen, stop),
                     daemon=True).start()
    try:
        shared = run_flow(flow_yaml, provider=hp)
    finally:
        stop.set()
        hp.close()

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
    assert read_pending(mailbox_dir(run_dir)) is None   # nothing left outstanding
    assert not (mailbox_dir(run_dir) / "heartbeat").exists()   # close() says "gone"


def test_unanswered_step_times_out(tmp_path):
    hp = HostProvider(tmp_path, timeout=0.05)
    with pytest.raises(HostTimeout, match="'s1'"):
        hp.run_step("s1", "sk", "sys", "do it", [])
    assert read_pending(tmp_path) is None             # pending cleared, not stale
    hp.close()


def test_submit_rejects_stale_or_duplicate_seq(tmp_path):
    d = _run_dir(tmp_path)
    with pytest.raises(ValueError, match="not pending"):
        submit(d, 1, "x")                             # nothing posted yet
    _post(d, 2)
    with pytest.raises(ValueError, match="outstanding: 2"):
        submit(d, 1, "x")                             # stale seq
    submit(d, 2, "ok")
    assert read_pending(mailbox_dir(d)) is None       # answered → not re-handed out
    with pytest.raises(ValueError):
        submit(d, 2, "again")                         # duplicate


def test_concurrent_submits_exactly_one_wins(tmp_path):
    # MCP runs sync tools on worker threads: two racing answers to one step
    for trial in range(20):
        d = _run_dir(tmp_path, name=f"r{trial}")
        _post(d, 1)
        ok, errs, go = [], [], threading.Barrier(2)
        def answer(text):
            go.wait()
            try:
                submit(d, 1, text)
                ok.append(text)
            except ValueError as e:
                errs.append(e)
        ts = [threading.Thread(target=answer, args=(t,)) for t in ("a", "b")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert len(ok) == 1 and len(errs) == 1, (ok, errs)   # never a crash/2 wins
        out = json.loads((mailbox_dir(d) / "result-1.json").read_text())["output"]
        assert out == ok[0]
        assert not list(mailbox_dir(d).glob(".tmp-*"))       # no tmp litter


def test_resume_drops_dead_engines_pending_step(tmp_path):
    # killed while waiting on seq 1: a late answer to that stale step must
    # never satisfy the resumed engine's (different) next step
    (tmp_path / "result-1.json").write_text('{"seq": 1, "output": "0.5"}')
    host._write_json(tmp_path / "pending.json", {"seq": 2, "step_id": "judge"})
    hp = HostProvider(tmp_path)
    assert hp._seq == 2                               # past results AND the stale step
    assert read_pending(tmp_path) is None             # stale step withdrawn
    hp.close()


def test_dead_run_steps_are_not_handed_out(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "STALE_AFTER", 0.2)  # crashed must hold this long
    d = _run_dir(tmp_path)
    _post(d, 1)
    _age(mailbox_dir(d) / "heartbeat", 10)        # engine was killed
    assert run_status(d) == "crashed"
    assert wait_next(d, timeout=1) == ("done", "crashed")      # not the stale step
    with pytest.raises(ValueError, match="not running"):
        submit(d, 1, "x")                             # no accepted-then-ignored work


def test_a_brief_stall_does_not_end_the_hosts_loop(tmp_path, monkeypatch):
    # laptop suspend / SIGSTOP: the beat goes stale for a moment, then resumes —
    # the host must keep waiting rather than declare the run crashed
    monkeypatch.setattr(host, "STALE_AFTER", 0.3)
    d = _run_dir(tmp_path)
    beat = mailbox_dir(d) / "heartbeat"
    _age(beat, 10)
    def revive():
        time.sleep(0.1)
        beat.touch()
        _post(d, 1)
    threading.Thread(target=revive).start()
    kind, val = wait_next(d, timeout=5)
    assert kind == "task" and val["seq"] == 1


def test_second_engine_cannot_hijack_a_live_mailbox(tmp_path):
    # e.g. a bare `saage resume` in another terminal picks the live run
    a = HostProvider(tmp_path)
    try:
        with pytest.raises(ValueError, match="another engine is live"):
            HostProvider(tmp_path)
    finally:
        a.close()
    HostProvider(tmp_path).close()                    # free once the first is gone


def test_answer_landing_at_the_deadline_is_kept(tmp_path, monkeypatch):
    hp = HostProvider(tmp_path, timeout=0.05)
    real_unlink = host._unlink
    def unlink_then_answer(path):                     # host answers in the gap
        if path.name == "pending.json" and not (tmp_path / "result-1.json").exists():
            (tmp_path / "result-1.json").write_text('{"seq": 1, "output": "just in time"}')
        real_unlink(path)
    monkeypatch.setattr(host, "_unlink", unlink_then_answer)
    try:
        assert hp.run_step("s1", "sk", "sys", "t", []) == "just in time"
    finally:
        hp.close()


def test_status_comes_from_the_run_itself(tmp_path):
    # a resumed run: checkpoint still says failed, but the engine is beating
    assert run_status(_run_dir(tmp_path, "resumed", status="failed")) == "running"
    assert run_status(_run_dir(tmp_path, "done", status="completed", beat=False)) == "completed"
    starting = _run_dir(tmp_path, "starting", beat=False)      # engine not up yet
    assert run_status(starting) == "running"
    _age(starting / "checkpoint.json", host.STARTUP_GRACE + 5)
    _age(starting, host.STARTUP_GRACE + 5)
    assert run_status(starting) == "crashed"                   # …and never came up


def test_resolve_run_never_guesses_between_live_runs(tmp_path):
    a = _run_dir(tmp_path, "20260101-a")
    with pytest.raises(ValueError, match="no hosted run matches"):
        resolve_run(tmp_path, "nope")
    assert resolve_run(tmp_path, None) == a           # the one live run
    assert resolve_run(tmp_path, "2026") == a         # unique prefix
    b = _run_dir(tmp_path, "20260101-b")
    with pytest.raises(ValueError, match="several hosted runs are live"):
        resolve_run(tmp_path, None)                   # an answer could land in either
    with pytest.raises(ValueError, match="matches several"):
        resolve_run(tmp_path, "2026")
    assert resolve_run(tmp_path, b.name) == b


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -5, None, "60", True])
def test_host_timeout_must_be_finite_positive(tmp_path, bad):
    with pytest.raises(ValueError, match="timeout must be a positive number"):
        HostProvider(tmp_path, timeout=bad)


def test_make_provider_host(tmp_path, monkeypatch):
    monkeypatch.delenv(host.MAILBOX_ENV, raising=False)
    with pytest.raises(ValueError, match="mailbox"):
        make_provider({"type": "host"})
    monkeypatch.setenv(host.MAILBOX_ENV, str(tmp_path / "mb"))
    # no model, no key; a flow can't redirect the mailbox (hosts couldn't find it)
    hp = make_provider({"type": "host", "timeout": 5, "mailbox": "./elsewhere"})
    assert isinstance(hp, HostProvider) and hp.timeout == 5
    assert hp.mailbox == tmp_path / "mb"
    hp.close()


def test_remote_handoff_refuses_host(tmp_path):
    from saage.remote.handoff import HandoffError, handoff
    f = tmp_path / "flow.yaml"
    f.write_text("provider: {type: host}\n"
                 "workflow:\n  - {id: a, type: command, run: 'true'}\n")
    with pytest.raises(HandoffError, match="can't be handed off"):
        handoff(flow=str(f), target=None)


def test_host_timeout_fails_run_cleanly(tmp_path):
    # nobody answers: `saage run` exits 1, marks the run failed, and says WHY
    # in run.log — where next_task/`saage host next` show the tail
    from saage import checkpoint as ckpt
    from saage.cli import _main
    f = tmp_path / "flow.yaml"
    shutil.copytree(GG / "propose", tmp_path / "propose")
    f.write_text("provider: {type: host, timeout: 0.1}\n"
                 "workflow:\n  - {id: propose, type: agent, skill: propose}\n")
    assert _main(["run", str(f), "--run-id", "t1"]) == 1
    run = ckpt.Checkpoint("t1")
    assert run.load()["status"] == "failed"
    assert "did not answer step 'propose'" in (run.dir / "run.log").read_text()


def test_host_start_resolves_workspace_against_cwd(tmp_path, monkeypatch, capsys):
    from saage.cli import _main
    from saage.server.jobs import JobRegistry
    got = {}
    monkeypatch.setattr(JobRegistry, "launch", lambda self, info, ov, workspace=None,
                        provider=None: got.update(ws=workspace, provider=provider)
                        or type("J", (), {"job_id": "j1"})())
    from saage.paths import runs_dir
    shutil.copytree(GG, tmp_path / "gg")
    monkeypatch.chdir(tmp_path)
    assert _main(["host", "start", "gg/flow.yaml", "--workspace", "myws"]) == 0
    assert got == {"ws": str(tmp_path / "myws"), "provider": "host"}
    assert mailbox_dir(runs_dir() / "j1").is_dir()   # findable before the child is up


def test_host_submit_missing_file_is_a_clean_error(tmp_path, capsys):
    from saage.cli import _main
    _run_dir(Path(os.environ["SAAGE_HOME"]) / "runs")
    assert _main(["host", "submit", "1", "--file", str(tmp_path / "nope.txt")]) == 1
    assert "can't read --file" in capsys.readouterr().err


def _drive(job_id, answers, capsys, monkeypatch):
    """Play the agent through `saage host next/submit`, keyed off exit codes."""
    from saage.cli import _main
    from saage.host_cli import EXIT_DONE, EXIT_STEP
    steps = []
    for _ in range(10):                           # bounded: 6 steps + done
        rc = _main(["host", "next", "--json", "--timeout", "30"])   # the live run
        out = capsys.readouterr().out
        if rc == EXIT_DONE:
            return steps, json.loads(out)
        assert rc == EXIT_STEP, (out, capsys.readouterr().err)
        step = json.loads(out)
        steps.append(step)
        monkeypatch.setattr("sys.stdin", io.StringIO(answers[step["skill"]].pop(0) + "\n"))
        assert _main(["host", "submit", job_id, str(step["seq"])]) == 0
    raise AssertionError("run never finished")


@posix_only
def test_host_cli_cycle(tmp_path, capsys, monkeypatch):
    # `saage host start/next/submit/status` against a real hosted subprocess
    from saage.cli import _main
    from saage.host_cli import EXIT_ERROR
    from saage.server.jobs import JobRegistry
    flow = tmp_path / "guessing_game"
    shutil.copytree(GG, flow)
    assert _main(["host", "start", str(flow / "flow.yaml"), "--set", "target=0.3"]) == 0
    job_id = capsys.readouterr().out.strip()
    try:
        steps, done = _drive(job_id, {"propose": ["0.5", "0.25", "0.3"],
                                      "judge": ["lower", "higher", "correct"]},
                             capsys, monkeypatch)
    finally:
        JobRegistry().cancel(job_id, grace=1)     # never leave a blocked child behind
    assert done == {"done": True, "status": "completed"}
    assert [s["step_id"] for s in steps] == ["propose", "judge"] * 3
    assert "hidden target is 0.3." in steps[1]["task"]      # engine rendered the knob
    assert (flow / "history.txt").read_text().splitlines() == [
        "guess=0.5 -> lower", "guess=0.25 -> higher", "guess=0.3 -> correct"]

    monkeypatch.setattr("sys.stdin", io.StringIO("x"))
    assert _main(["host", "submit", "1"]) == EXIT_ERROR        # run is finished
    assert "not running" in capsys.readouterr().err
    assert _main(["host", "status", job_id[:12]]) == 0         # prefix resolves
    assert "completed" in capsys.readouterr().out


@posix_only
def test_plain_saage_run_host_is_drivable(tmp_path, capsys, monkeypatch):
    # a host-mode run nobody registered as a job (`saage run --provider host`
    # in another terminal) is found through the run store, not the job registry
    flow = tmp_path / "guessing_game"
    shutil.copytree(GG, flow)
    proc = subprocess.Popen(
        [sys.executable, "-m", "saage.cli", "run", str(flow / "flow.yaml"),
         "--provider", "host", "--run-id", "plain1", "--set", "target=0.5"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
    try:
        mb = mailbox_dir(Path(os.environ["SAAGE_HOME"]) / "runs" / "plain1")
        for _ in range(300):                      # the "other terminal" starts up
            if mb.is_dir():
                break
            time.sleep(0.05)
        steps, done = _drive("plain1", {"propose": ["0.5"], "judge": ["correct"]},
                             capsys, monkeypatch)
        assert proc.wait(timeout=30) == 0
    finally:
        proc.kill()
    assert done == {"done": True, "status": "completed"}
    assert [s["step_id"] for s in steps] == ["propose", "judge"]


def test_host_cli_no_run(capsys):
    from saage.cli import _main
    assert _main(["host", "next"]) == 1
    assert "no hosted run found" in capsys.readouterr().err
