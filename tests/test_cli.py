"""CLI: --set JSON coercion + end-to-end run of a command-only flow."""
from saage.cli import _parse_set, main


def test_parse_set_coerces_json_values():
    s = _parse_set(["a=1", "b=0.5", "c=true", "d=hello", "e=null", "f=a=b"])
    assert s == {"a": 1, "b": 0.5, "c": True, "d": "hello", "e": None, "f": "a=b"}


def test_main_runs_command_flow(tmp_path):
    flow = tmp_path / "flow.yaml"
    flow.write_text(
        "provider: { type: openai, model: x }\n"
        "shared: { greeting: hi }\n"
        "workflow:\n"
        '  - { id: say, type: command, run: "echo {{ greeting }} {{ n }} > out.txt" }\n')
    ws = tmp_path / "ws"
    rc = main(["run", str(flow), "--workspace", str(ws), "--set", "n=42", "-q"])
    assert rc == 0
    assert (ws / "out.txt").read_text().strip() == "hi 42"   # shared + --set rendered


def test_provider_failure_is_one_line_not_traceback(tmp_path, caplog, capsys):
    # a provider that can't be reached (here: a local server on a closed port)
    # must end the run with a clean one-line error; the traceback goes to
    # run.log, never the console
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "skill.md").write_text("---\n---\nSKILL_ID: s\n")
    flow = tmp_path / "flow.yaml"
    flow.write_text(
        "provider: { type: local, model: m, base_url: 'http://127.0.0.1:1/v1',\n"
        "            retry: { max_attempts: 1, base_delay: 0 } }\n"
        "workflow:\n"
        "  - { id: a, type: agent, skill: s }\n")
    rc = main(["run", str(flow), "--workspace", str(tmp_path / "ws"), "-q"])
    assert rc == 1
    assert "Traceback" not in capsys.readouterr().err
    assert any("provider call failed" in r.message for r in caplog.records)
    from saage import checkpoint as ckpt
    runs = ckpt.list_runs()
    rec = runs[0].load()
    assert rec["status"] == "failed"
    run_log = (runs[0].dir / "run.log").read_text()
    assert "Traceback" in run_log            # full detail preserved on disk
