"""run_shell: flow commands get POSIX-sh semantics on every OS (Git Bash on
native Windows, /bin/sh elsewhere). These tests run on both platforms — on
Windows they are the regression suite for the cmd.exe failure modes the
baseline caught (see docs/windows_support_plan.md §1)."""
import os
import subprocess

import pytest

from saage.shell import find_bash, run_shell


def test_single_quotes_and_append_redirect(tmp_path):
    # the exact native-Windows failure mode (F1): under cmd.exe the '>' inside
    # the single quotes became a redirect and wrote a file named `higher'`
    r = run_shell("echo 'guess=0.5 -> higher' >> history.txt", cwd=tmp_path)
    assert r.returncode == 0
    text = (tmp_path / "history.txt").read_text(encoding="utf-8")
    assert text.strip() == "guess=0.5 -> higher"
    assert not (tmp_path / "higher'").exists()


def test_dollar_var_expansion_from_env(tmp_path):
    env = os.environ.copy()
    env["SAAGE_TEST_VAR"] = "hello-from-env"
    r = run_shell("echo $SAAGE_TEST_VAR", cwd=tmp_path, env=env)
    assert "hello-from-env" in r.stdout


def test_env_prefix_assignment(tmp_path):
    # the `STABLEWM_HOME="..." python ...` convention the flows rely on
    r = run_shell("X=prefixed sh -c 'echo $X'", cwd=tmp_path)
    assert "prefixed" in r.stdout


def test_chaining_and_exit_codes(tmp_path):
    assert run_shell("true && echo yes", cwd=tmp_path).stdout.strip() == "yes"
    assert run_shell("false", cwd=tmp_path).returncode != 0
    assert run_shell("false || echo rescued", cwd=tmp_path).stdout.strip() == "rescued"


def test_embedded_double_quotes_survive_argv_roundtrip(tmp_path):
    # on Windows the command travels as one argv element through CreateProcess
    # quoting (list2cmdline) — embedded \" must come out intact
    r = run_shell('echo "she said \\"hi\\""', cwd=tmp_path)
    assert 'she said "hi"' in r.stdout


def test_backslash_path_as_data(tmp_path):
    # a quoted Windows path inside a command is data, not escapes
    # (printf %s: unlike echo, never interprets backslashes in the argument)
    r = run_shell(r'printf "%s\n" "C:\Users\nobody"', cwd=tmp_path)
    assert r"C:\Users\nobody" in r.stdout


def test_utf8_output_is_captured(tmp_path):
    r = run_shell("printf '%s\\n' 'café ✓'", cwd=tmp_path)
    assert "café ✓" in r.stdout


def test_cwd_is_the_workspace(tmp_path):
    run_shell("echo marker > here.txt", cwd=tmp_path)
    assert (tmp_path / "here.txt").exists()


# --------------------------------------------------------------------------- #
# bash discovery (the Windows-only path)
# --------------------------------------------------------------------------- #
def test_saage_shell_override_wins(monkeypatch):
    import sys
    monkeypatch.setenv("SAAGE_SHELL", sys.executable)   # any real executable
    find_bash.cache_clear()
    try:
        assert find_bash() == sys.executable
    finally:
        find_bash.cache_clear()


def test_saage_shell_bogus_override_is_rejected(monkeypatch):
    # a typo'd override must fail with the curated message, not a confusing
    # per-command FileNotFoundError
    from saage.shell import ShellNotFound
    monkeypatch.setenv("SAAGE_SHELL", "/no/such/bash-xyz")
    find_bash.cache_clear()
    try:
        with pytest.raises(ShellNotFound, match="SAAGE_SHELL"):
            find_bash()
    finally:
        find_bash.cache_clear()


def test_saage_shell_cmd_sentinel_accepted_anywhere(monkeypatch):
    # 'cmd', 'cmd.exe', or a full path to it — all mean the escape hatch
    for value in ("cmd", "cmd.exe", r"C:\Windows\System32\cmd.exe"):
        monkeypatch.setenv("SAAGE_SHELL", value)
        find_bash.cache_clear()
        try:
            assert find_bash() == value
        finally:
            find_bash.cache_clear()


@pytest.mark.skipif(os.name != "nt", reason="bash discovery matters on Windows only")
def test_find_bash_finds_a_working_non_wsl_bash():
    find_bash.cache_clear()
    bash = find_bash()
    assert "system32" not in bash.lower()      # never the WSL launcher
    out = subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True)
    assert out.stdout.strip() == "ok"


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe escape hatch is Windows-only")
def test_saage_shell_cmd_forces_legacy_cmd(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAGE_SHELL", "cmd")
    find_bash.cache_clear()
    try:
        r = run_shell("echo %CD%", cwd=tmp_path)     # a cmd-ism bash won't expand
        assert str(tmp_path) in r.stdout
    finally:
        find_bash.cache_clear()
