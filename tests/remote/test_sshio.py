"""SSHConn transport invariants: binary-safe stdin (no CRLF corruption from a
Windows local machine) and the tar-over-ssh fallback used when rsync is absent
(Git for Windows doesn't bundle rsync)."""
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from saage.remote.sshio import SSHConn, _excluded, _pack_dir


# --------------------------------------------------------------------------- #
# stdin must never be newline-translated
# --------------------------------------------------------------------------- #
def test_run_sends_binary_stdin(monkeypatch):
    seen = {}

    def fake_run(argv, input=None, capture_output=True, timeout=None):
        seen["input"] = input
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    conn = SSHConn(host="node")
    conn.run("cat > x", input="#!/bin/sh\necho hi\n")
    assert isinstance(seen["input"], bytes)
    assert b"\r" not in seen["input"]              # the whole point
    assert seen["input"] == b"#!/bin/sh\necho hi\n"


def test_run_decodes_output_as_utf8(monkeypatch):
    def fake_run(argv, input=None, capture_output=True, timeout=None):
        return subprocess.CompletedProcess(argv, 0, "café ✓\n".encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert SSHConn(host="node").run("echo").stdout == "café ✓\n"


# --------------------------------------------------------------------------- #
# exclude semantics (rsync-style component matching)
# --------------------------------------------------------------------------- #
def test_excluded_matches_any_path_component():
    ex = (".git", "__pycache__", "*.log", "tests")
    assert _excluded(".git/config", ex)
    assert _excluded("saage/__pycache__/x.pyc", ex)
    assert _excluded("deep/run.log", ex)
    assert _excluded("tests/test_x.py", ex)
    assert not _excluded("saage/tools.py", ex)
    assert not _excluded("testsuite/x.py", ex)     # 'tests' is not 'testsuite'


def test_pack_dir_roundtrip_with_excludes(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "mod.py").write_text("x = 1\n", newline="\n")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref\n")
    (src / "noise.log").write_text("zzz\n")

    blob = _pack_dir(src, (".git", "*.log"))
    out = tmp_path / "out"
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        tf.extractall(out, filter="data")
    assert (out / "pkg" / "mod.py").read_bytes() == b"x = 1\n"   # LF intact
    assert not (out / ".git").exists()
    assert not (out / "noise.log").exists()


# --------------------------------------------------------------------------- #
# tar fallback wiring (rsync forced off; the ssh hop is stubbed)
# --------------------------------------------------------------------------- #
@pytest.fixture
def tar_mode(monkeypatch):
    monkeypatch.setenv("SAAGE_FORCE_TAR", "1")


def test_rsync_to_dir_falls_back_to_tar(tmp_path, tar_mode, monkeypatch):
    src = tmp_path / "engine"
    src.mkdir()
    (src / "a.py").write_text("a\n", newline="\n")
    calls = []
    monkeypatch.setattr(SSHConn, "run",
                        lambda self, cmd, **kw: calls.append((cmd, kw)) or
                        subprocess.CompletedProcess([], 0, "", ""))
    conn = SSHConn(host="node")
    conn.rsync_to(str(src) + "/", "run/saage/", delete=True)

    cmd, kw = calls[0]
    assert "rm -rf run/saage && " in cmd            # delete=True wipes first
    assert "tar -xzf - -C run/saage" in cmd
    with tarfile.open(fileobj=io.BytesIO(kw["input"]), mode="r:gz") as tf:
        assert "a.py" in tf.getnames()


def test_rsync_to_file_falls_back_to_cat(tmp_path, tar_mode, monkeypatch):
    f = tmp_path / "ws.bundle"
    f.write_bytes(b"\x00\x01bundle")
    calls = []
    monkeypatch.setattr(SSHConn, "run",
                        lambda self, cmd, **kw: calls.append((cmd, kw)) or
                        subprocess.CompletedProcess([], 0, "", ""))
    SSHConn(host="node").rsync_to(f, "run/ws.bundle")
    cmd, kw = calls[0]
    assert cmd == "cat > run/ws.bundle"
    assert kw["input"] == b"\x00\x01bundle"         # binary-exact


def test_rsync_from_dir_falls_back_to_tar(tmp_path, tar_mode, monkeypatch):
    payload = _pack_dir_payload()

    def fake_run(self, cmd, **kw):
        if cmd.startswith("test -d"):
            return subprocess.CompletedProcess([], 0, "", "")
        assert "tar -czf -" in cmd
        return subprocess.CompletedProcess([], 0, payload, "")

    monkeypatch.setattr(SSHConn, "run", fake_run)
    dest = tmp_path / "results"
    SSHConn(host="node").rsync_from("run/artifacts/", dest)
    assert (dest / "experiments.jsonl").read_bytes() == b'{"x": 1}\n'


def test_rsync_from_file_falls_back_to_cat(tmp_path, tar_mode, monkeypatch):
    def fake_run(self, cmd, **kw):
        if cmd.startswith("test -d"):
            return subprocess.CompletedProcess([], 1, "", "")
        assert cmd == "cat run/saage.log"
        return subprocess.CompletedProcess([], 0, b"line1\nline2\n", "")

    monkeypatch.setattr(SSHConn, "run", fake_run)
    dest = tmp_path / "results"
    SSHConn(host="node").rsync_from("run/saage.log", dest)
    assert (dest / "saage.log").read_bytes() == b"line1\nline2\n"


def _pack_dir_payload() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b'{"x": 1}\n'
        info = tarfile.TarInfo("experiments.jsonl")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()
