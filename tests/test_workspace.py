"""Configurable workspace root + automatic venv activation for commands."""
from cwe.hydrate import run_flow
from cwe.tools import file_tools


def _write_flow(flow_dir, run_cmd):
    flow_dir.mkdir(parents=True, exist_ok=True)
    (flow_dir / "flow.yaml").write_text(
        "provider: { type: openai, model: x }\n"
        "workflow:\n"
        f"  - {{ id: touch, type: command, run: '{run_cmd}' }}\n")
    return flow_dir / "flow.yaml"


# --------------------------------------------------------------------------- #
# workspace root
# --------------------------------------------------------------------------- #
def test_workspace_redirects_commands(tmp_path):
    flow_yaml = _write_flow(tmp_path / "flow", "echo hi > made.txt")
    ws = tmp_path / "ws"
    shared = run_flow(flow_yaml, provider=object(), workspace=str(ws))
    assert shared["workspace"] == str(ws)
    assert (ws / "made.txt").exists()                 # command ran in the workspace
    assert not (flow_yaml.parent / "made.txt").exists()  # not in the flow dir


def test_workspace_defaults_to_flow_dir(tmp_path):
    flow_yaml = _write_flow(tmp_path / "flow", "echo hi > made.txt")
    shared = run_flow(flow_yaml, provider=object())
    assert shared["workspace"] == str(flow_yaml.parent)
    assert (flow_yaml.parent / "made.txt").exists()


def test_workspace_created_if_missing(tmp_path):
    flow_yaml = _write_flow(tmp_path / "flow", "echo hi > made.txt")
    ws = tmp_path / "deep" / "nested" / "ws"          # does not exist yet
    run_flow(flow_yaml, provider=object(), workspace=str(ws))
    assert ws.is_dir() and (ws / "made.txt").exists()


# --------------------------------------------------------------------------- #
# venv activation
# --------------------------------------------------------------------------- #
def _run_command(tools):
    return {t.name: t for t in tools}["run_command"]


def test_venv_activated_when_present(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)   # venv exists
    out = _run_command(file_tools(tmp_path, venv=".venv")).run(command="echo $VIRTUAL_ENV")
    assert str(tmp_path / ".venv") in out


def test_venv_puts_its_bin_first_on_path(tmp_path):
    venvbin = tmp_path / ".venv" / "bin"
    venvbin.mkdir(parents=True)
    fake = venvbin / "python"
    fake.write_text("#!/bin/sh\necho FROM_VENV_PYTHON\n")
    fake.chmod(0o755)
    out = _run_command(file_tools(tmp_path, venv=".venv")).run(command="python")
    assert "FROM_VENV_PYTHON" in out                   # resolved to the venv python


def test_venv_ignored_when_absent(tmp_path):
    # no .venv on disk yet (e.g. before `setup`) -> command runs plain
    out = _run_command(file_tools(tmp_path, venv=".venv")).run(command="echo $VIRTUAL_ENV")
    assert str(tmp_path / ".venv") not in out
