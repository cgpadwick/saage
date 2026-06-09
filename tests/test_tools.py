import subprocess

import pytest

from cwe.tools import default_tools, file_tools, git_tools


def _by_name(tools):
    return {t.name: t for t in tools}


def test_file_crud(tmp_path):
    t = _by_name(file_tools(tmp_path))
    assert "wrote" in t["write_file"].run(path="a/b.txt", content="hello")
    assert t["read_file"].run(path="a/b.txt") == "hello"
    t["edit_file"].run(path="a/b.txt", old="hello", new="world")
    assert t["read_file"].run(path="a/b.txt") == "world"
    t["delete_file"].run(path="a/b.txt")
    assert not (tmp_path / "a/b.txt").exists()


def test_edit_requires_unique_match(tmp_path):
    t = _by_name(file_tools(tmp_path))
    t["write_file"].run(path="x.txt", content="aa")
    with pytest.raises(ValueError):
        t["edit_file"].run(path="x.txt", old="a", new="b")   # matches twice


def test_sandbox_blocks_escape(tmp_path):
    t = _by_name(file_tools(tmp_path))
    with pytest.raises(ValueError):
        t["read_file"].run(path="../../etc/passwd")


def test_run_command(tmp_path):
    t = _by_name(file_tools(tmp_path))
    out = t["run_command"].run(command="echo hi")
    assert "exit=0" in out and "hi" in out


def test_run_command_timeout(tmp_path):
    t = _by_name(file_tools(tmp_path))
    with pytest.raises(subprocess.TimeoutExpired):
        t["run_command"].run(command="sleep 5", timeout=1)


def test_git_tools(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    g = _by_name(git_tools(tmp_path))
    (tmp_path / "f.txt").write_text("hi")
    assert "f.txt" in g["git_status"].run()
    g["git_add"].run(paths="f.txt")
    g["git_commit"].run(message="init")
    assert "init" in g["git_log"].run(n=5)


def test_append_file(tmp_path):
    t = _by_name(file_tools(tmp_path))
    t["append_file"].run(path="log.txt", content="line1\n")   # creates the file
    t["append_file"].run(path="log.txt", content="line2\n")   # appends
    assert (tmp_path / "log.txt").read_text() == "line1\nline2\n"


def test_default_tools_count(tmp_path):
    names = [t.name for t in default_tools(tmp_path)]
    assert names == [
        "read_file", "write_file", "append_file", "edit_file", "delete_file",
        "run_command", "git_status", "git_diff", "git_add", "git_commit",
        "git_branch", "git_checkout", "git_log"]
