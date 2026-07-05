"""H6: content-keyed, locked, stamped node provisioning.

The generated provision script is executed with REAL bash against a temp
$HOME — the lock/stamp/idempotence semantics are what's under test, not a
transcript of the script text. Network/ssh never involved.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from saage.remote.provision import _provision_script, provision_key
from saage.remote.scripts import RunSpec, bootstrap_sh

pytestmark = pytest.mark.skipif(os.name != "posix", reason="bash + flock")


def _run(script: str, home: Path, command_log: Path | None = None):
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env={**os.environ, "HOME": str(home)})


def test_first_run_provisions_then_caches(tmp_path):
    script = _provision_script('echo pulled >> "$HOME/pulls"',
                               key="k1", force=False)
    first = _run(script, tmp_path)
    assert first.returncode == 0 and "PROVISION_OK" in first.stdout
    second = _run(script, tmp_path)
    assert "PROVISION_CACHED" in second.stdout
    # the expensive command ran exactly once
    assert (tmp_path / "pulls").read_text() == "pulled\n"
    assert (tmp_path / ".saage_cache" / ".stamps" / "k1.ready").exists()


def test_force_drops_stamp_and_reruns(tmp_path):
    script = _provision_script('echo pulled >> "$HOME/pulls"', key="k1", force=False)
    _run(script, tmp_path)
    forced = _provision_script('echo pulled >> "$HOME/pulls"', key="k1", force=True)
    out = _run(forced, tmp_path)
    assert "PROVISION_OK" in out.stdout
    assert (tmp_path / "pulls").read_text() == "pulled\npulled\n"


def test_failure_leaves_no_stamp(tmp_path):
    script = _provision_script("false", key="bad", force=False)
    out = _run(script, tmp_path)
    assert out.returncode != 0
    assert not (tmp_path / ".saage_cache" / ".stamps" / "bad.ready").exists()
    # and the next attempt actually retries (no poisoned cache)
    ok = _run(_provision_script("true", key="bad", force=False), tmp_path)
    assert "PROVISION_OK" in ok.stdout


def test_concurrent_callers_serialize_one_does_the_work(tmp_path):
    """K simultaneous cold starts = 1 pull + K-1 cheap waits (the flock)."""
    script = _provision_script(
        'sleep 0.3; echo pulled >> "$HOME/pulls"', key="k1", force=False)
    procs = [subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE,
                              text=True, env={**os.environ, "HOME": str(tmp_path)})
             for _ in range(4)]
    outs = [p.communicate()[0] for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert (tmp_path / "pulls").read_text() == "pulled\n"          # exactly once
    assert sum("PROVISION_OK" in o for o in outs) == 1
    assert sum("PROVISION_CACHED" in o for o in outs) == 3


def test_command_runs_in_keyed_cwd_with_cache_exported(tmp_path):
    script = _provision_script('pwd > "$HOME/where"; echo "$SAAGE_CACHE" > "$HOME/cache"',
                               key="kx", force=False)
    assert _run(script, tmp_path).returncode == 0
    assert (tmp_path / "where").read_text().strip().endswith(
        ".saage_cache/provision/kx")
    assert (tmp_path / "cache").read_text().strip() == str(
        tmp_path / ".saage_cache")


def test_env_file_is_sourced_when_present(tmp_path):
    pdir = tmp_path / ".saage_cache" / "provision" / "ke"
    pdir.mkdir(parents=True)
    (pdir / "env").write_text("MY_TOKEN=sekrit\n")
    script = _provision_script('echo "$MY_TOKEN" > "$HOME/tok"', key="ke", force=False)
    assert _run(script, tmp_path).returncode == 0
    assert (tmp_path / "tok").read_text().strip() == "sekrit"


def test_provision_key_is_stable_and_content_derived():
    assert provision_key("pull dataset X") == provision_key("pull dataset X")
    assert provision_key("pull dataset X") != provision_key("pull dataset Y")


# ---- bootstrap integration: the (a)-mode backstop ---------------------------

def test_bootstrap_exports_cache_and_locks_ws_setup():
    spec = RunSpec(run_id="r1", flow_file="flow.yaml", ws_mode="ephemeral",
                   ws_setup="bash ../flow/cloud_setup.sh")
    script = bootstrap_sh(spec)
    assert 'export SAAGE_CACHE="$HOME/.saage_cache"' in script
    assert "flock" in script
    assert "'bash ../flow/cloud_setup.sh'" in script    # quoted through bash -c


def test_bootstrap_without_ws_setup_has_no_lock():
    spec = RunSpec(run_id="r1", flow_file="flow.yaml", ws_mode="ephemeral")
    script = bootstrap_sh(spec)
    assert "flock" not in script
    assert 'export SAAGE_CACHE' in script               # cache always exported
