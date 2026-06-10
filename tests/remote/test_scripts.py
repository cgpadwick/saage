import subprocess

import pytest

from saage.remote.scripts import RunSpec, bootstrap_sh, start_sh, stop_sh


def _spec(**kw) -> RunSpec:
    defaults = dict(run_id="flow-20260609-1200-abcd", flow_file="flow.yaml",
                    ws_mode="ephemeral")
    defaults.update(kw)
    return RunSpec(**defaults)


def _bash_n(script: str) -> None:
    proc = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
    assert proc.returncode == 0, f"bash -n rejected the script:\n{proc.stderr}"


@pytest.mark.parametrize("ws_mode", ["ephemeral", "branch", "bundle"])
def test_all_scripts_are_valid_bash(ws_mode):
    spec = _spec(ws_mode=ws_mode)
    for script in (bootstrap_sh(spec), start_sh(spec), stop_sh(spec)):
        _bash_n(script)


def test_bootstrap_ws_modes():
    assert "mkdir -p ws" in bootstrap_sh(_spec(ws_mode="ephemeral"))
    assert '"$WS_REPO_URL" ws' in bootstrap_sh(_spec(ws_mode="branch"))
    assert "./ws.bundle ws" in bootstrap_sh(_spec(ws_mode="bundle"))


def test_set_args_are_shell_quoted():
    spec = _spec(set_args={"task": "do things; rm -rf /", "epochs": 8})
    script = start_sh(spec)
    assert "--set 'task=do things; rm -rf /'" in script
    assert "--set epochs=8" in script
    _bash_n(script)


def test_watchdog_and_sync_intervals():
    spec = _spec(max_run_days=0.5, sync_interval=60)
    script = start_sh(spec)
    assert f"sleep {int(0.5 * 86400)}" in script
    assert "sleep 60" in script


def test_run_dir_and_session_naming():
    spec = _spec()
    assert spec.run_dir.endswith(f".saage_runs/{spec.run_id}")
    assert spec.session == f"saage-{spec.run_id}"
    assert spec.session in stop_sh(spec)


def test_secrets_never_outlive_the_run():
    assert "rm -f run_env" in start_sh(_spec())
    assert "rm -f run_env" in stop_sh(_spec())


def test_final_status_is_written_before_the_last_mirror_push():
    # regression: the bucket must see the final phase — status done/failed has
    # to land in status.json BEFORE the closing collect() mirrors it out
    script = start_sh(_spec(r2=True))
    tail = script[script.index('kill "$SIDECAR"'):]
    assert tail.index("status done") < tail.index("\ncollect")   # the call, not the comment
    assert "status killed" not in tail  # stop.sh owns the killed phase


def test_venv_flag_passthrough():
    assert "--venv .venv-custom" in start_sh(_spec(venv_arg=".venv-custom"))
    assert "--venv" not in start_sh(_spec())


def test_ws_setup_hook_runs_in_ws_after_clone():
    script = bootstrap_sh(_spec(ws_mode="bundle", ws_setup="bash ../flow/cloud_setup.sh"))
    hook = "( cd ws && bash ../flow/cloud_setup.sh )"
    assert hook in script
    assert script.index("./ws.bundle ws") < script.index(hook)   # clone first
    assert script.index(hook) < script.index("BOOTSTRAP_OK")
    assert "( cd ws &&" not in bootstrap_sh(_spec())             # absent by default
    _bash_n(script)
