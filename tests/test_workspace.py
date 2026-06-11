"""Configurable workspace root + automatic venv activation for commands."""
import os

from saage.hydrate import run_flow
from saage.tools import file_tools, venv_env

# the platform's venv executables dir: what `python -m venv` creates here
VENV_BIN = "Scripts" if os.name == "nt" else "bin"


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
    assert shared["workspace"] == str(ws.resolve())   # canonicalized absolute path
    assert (ws / "made.txt").exists()                 # command ran in the workspace
    assert not (flow_yaml.parent / "made.txt").exists()  # not in the flow dir


def test_workspace_defaults_to_flow_dir(tmp_path):
    flow_yaml = _write_flow(tmp_path / "flow", "echo hi > made.txt")
    shared = run_flow(flow_yaml, provider=object())
    assert shared["workspace"] == str(flow_yaml.parent.resolve())
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
    (tmp_path / ".venv" / VENV_BIN).mkdir(parents=True)   # venv exists
    out = _run_command(file_tools(tmp_path, venv=".venv")).run(command="echo $VIRTUAL_ENV")
    assert str(tmp_path / ".venv") in out


def test_venv_puts_its_bin_first_on_path(tmp_path):
    venvbin = tmp_path / ".venv" / VENV_BIN
    venvbin.mkdir(parents=True)
    fake = venvbin / "python"
    fake.write_text("#!/bin/sh\necho FROM_VENV_PYTHON\n", newline="\n")
    fake.chmod(0o755)
    out = _run_command(file_tools(tmp_path, venv=".venv")).run(command="python")
    assert "FROM_VENV_PYTHON" in out                   # resolved to the venv python


def test_venv_env_handles_both_layouts(tmp_path):
    # a POSIX-layout venv and a Windows-layout venv both activate, on any host
    (tmp_path / "v1" / "bin").mkdir(parents=True)
    (tmp_path / "v2" / "Scripts").mkdir(parents=True)
    e1, e2 = venv_env(tmp_path, "v1"), venv_env(tmp_path, "v2")
    assert e1["PATH"].startswith(str(tmp_path / "v1" / "bin") + os.pathsep)
    assert e2["PATH"].startswith(str(tmp_path / "v2" / "Scripts") + os.pathsep)
    assert e1["VIRTUAL_ENV"] == str(tmp_path / "v1")
    assert e2["VIRTUAL_ENV"] == str(tmp_path / "v2")


def test_venv_ignored_when_absent(tmp_path):
    # no .venv on disk yet (e.g. before `setup`) -> command runs plain
    out = _run_command(file_tools(tmp_path, venv=".venv")).run(command="echo $VIRTUAL_ENV")
    assert str(tmp_path / ".venv") not in out


# --------------------------------------------------------------------------- #
# the auto-seeded {{ python }} interpreter launcher
# --------------------------------------------------------------------------- #
def test_python_var_seeded_per_platform(tmp_path):
    # the flows' `{{ python }}` helper-invocation convention rests on this seed
    flow_yaml = _write_flow(tmp_path / "flow", "echo hi")
    shared = run_flow(flow_yaml, provider=object())
    assert shared["python"] == ("python" if os.name == "nt" else "python3")


def test_python_var_overridable_in_shared(tmp_path):
    flow_dir = tmp_path / "flow"
    flow_dir.mkdir(parents=True)
    (flow_dir / "flow.yaml").write_text(
        "provider: { type: openai, model: x }\n"
        "shared: { python: python3.12 }\n"
        "workflow:\n  - { id: t, type: command, run: 'echo hi' }\n")
    shared = run_flow(flow_dir / "flow.yaml", provider=object())
    assert shared["python"] == "python3.12"
