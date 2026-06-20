"""R2 mirror layer: config parsing, upload planning, script gating.

All offline. A live roundtrip against a real bucket is in
test_r2_live.py (gated on SAAGE_R2_TESTS=1 + configured [storage]).
"""
import json
from pathlib import Path

from saage.remote.creds import cred_path, storage_config
from saage.remote.handoff import _collect_secrets
from saage.remote.r2push import changed, plan_uploads, record
from saage.remote.scripts import RunSpec, bootstrap_sh, start_sh, stop_sh
from saage.remote.workspace import WorkspacePlan

R2_TOML = """\
[storage]
endpoint = "https://acct.r2.cloudflarestorage.com"
bucket = "saage-data"
access_key = "AKIA123"
secret_key = "shh"
"""


def _write_creds(saage_home, body: str) -> None:
    cred_path().write_text(body)
    cred_path().chmod(0o600)


def test_no_storage_section_means_none(saage_home):
    _write_creds(saage_home, "[targets.a]\nhost = 'h'\n")
    assert storage_config() is None


def test_placeholders_mean_not_configured(saage_home):
    _write_creds(saage_home, R2_TOML.replace("AKIA123", "<paste-access-key-id>"))
    assert storage_config() is None


def test_storage_parses_and_prefixes(saage_home):
    _write_creds(saage_home, R2_TOML)
    s = storage_config()
    assert s is not None
    assert s.bucket == "saage-data"
    assert s.region == "auto"
    assert s.run_prefix("run-1") == "runs/run-1"


def test_plan_uploads(tmp_path):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "experiments.jsonl").write_text("{}\n")
    (tmp_path / "artifacts" / "report.html").write_text("<html>")
    (tmp_path / "status.json").write_text('{"phase":"running"}')
    (tmp_path / "saage.log").write_text("log")
    (tmp_path / "run_env").write_text("SECRET=1")          # must never be uploaded

    pairs = plan_uploads(tmp_path, "runs/r1")
    keys = [k for _, k in pairs]
    assert keys == ["runs/r1/artifacts/experiments.jsonl",
                    "runs/r1/artifacts/report.html",
                    "runs/r1/status.json",
                    "runs/r1/saage.log"]
    assert not any("run_env" in k for k in keys)


def test_scripts_gate_r2_on_spec():
    on = RunSpec(run_id="r", flow_file="f.yaml", ws_mode="ephemeral", r2=True)
    off = RunSpec(run_id="r", flow_file="f.yaml", ws_mode="ephemeral", r2=False)
    assert "boto3" in bootstrap_sh(on)
    assert "boto3" not in bootstrap_sh(off)
    # collect()'s mirror call is env-gated, so it appears either way but only
    # fires when SAAGE_R2_BUCKET is in run_env
    for script in (start_sh(on), stop_sh(on)):
        assert "saage.remote.r2push" in script
        assert "SAAGE_R2_BUCKET" in script


def test_secrets_include_r2_only_when_configured(saage_home):
    plan = WorkspacePlan(mode="ephemeral")
    _write_creds(saage_home, R2_TOML)
    s = storage_config()
    env = _collect_secrets("local", plan, {}, "r1", s)
    assert env["SAAGE_R2_BUCKET"] == "saage-data"
    assert env["SAAGE_R2_PREFIX"] == "runs/r1"
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA123"
    env = _collect_secrets("local", plan, {}, "r1", None)
    assert "SAAGE_R2_BUCKET" not in env
    assert "AWS_ACCESS_KEY_ID" not in env


def test_plan_uploads_includes_checkpoint(tmp_path, monkeypatch):
    # node layout: run dir + ~/.saage/runs/<id>/checkpoint.json
    run = tmp_path / "rundir"; (run / "artifacts").mkdir(parents=True)
    (run / "status.json").write_text("{}")
    home = tmp_path / "home"
    monkeypatch.setenv("SAAGE_HOME", str(home / ".saage"))
    ckdir = home / ".saage" / "runs" / "r1"; ckdir.mkdir(parents=True)
    (ckdir / "checkpoint.json").write_text('{"status":"running"}')
    keys = [k for _, k in plan_uploads(run, "runs/r1", run_id="r1")]
    assert "runs/r1/checkpoint.json" in keys
    assert "runs/r1/status.json" in keys


def test_changed_only_skips_unchanged(tmp_path):
    run = tmp_path / "rundir"; (run / "artifacts").mkdir(parents=True)
    big = run / "artifacts" / "model.pt"; big.write_bytes(b"x" * 1000)
    man = run / ".r2push_manifest.json"
    pairs = [(big, "runs/r1/artifacts/model.pt")]
    todo1 = changed(pairs, man)
    assert todo1 == pairs                      # first time: upload
    record(todo1, man)
    assert changed(pairs, man) == []    # unchanged: skip
    big.write_bytes(b"y" * 2000)               # changed size
    assert changed(pairs, man) == pairs # changed: upload again
