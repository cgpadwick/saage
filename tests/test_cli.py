"""CLI: --set JSON coercion + end-to-end run of a command-only flow."""
from cwe.cli import _parse_set, main


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
