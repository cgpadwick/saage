import os
from pathlib import Path

import pytest

from saage.remote.creds import (CredsError, add_target, cred_path, get_target,
                                list_targets, load_creds, remove_target)

# POSIX file modes don't exist on NTFS: chmod is a no-op and stat reports
# 0o666, so the 0600 check (and these tests of it) are POSIX-only
_posix_only = pytest.mark.skipif(os.name != "posix",
                                 reason="POSIX file-mode semantics")


def test_saage_home_env_relocates_creds(saage_home):
    assert cred_path() == saage_home / "credentials.toml"


def test_add_and_get_target_roundtrip(saage_home):
    add_target("spark", "spark.local", user="saage", hourly_usd=1.29)
    t = get_target("spark")
    assert t.host == "spark.local"
    assert t.user == "saage"
    assert t.hourly_usd == 1.29
    assert t.port == 22
    assert t.key == saage_home / "ssh" / "saage_ed25519"


@_posix_only
def test_creds_file_created_0600(saage_home):
    add_target("a", "h1")
    assert (cred_path().stat().st_mode & 0o077) == 0


@_posix_only
def test_refuses_world_readable_creds(saage_home):
    add_target("a", "h1")
    cred_path().chmod(0o644)
    with pytest.raises(CredsError, match="chmod 600"):
        load_creds()


def test_duplicate_target_rejected(saage_home):
    add_target("a", "h1")
    with pytest.raises(CredsError, match="already exists"):
        add_target("a", "h2")


def test_unknown_target_lists_known(saage_home):
    add_target("spark", "spark.local")
    with pytest.raises(CredsError, match="spark"):
        get_target("nope")


def test_foreign_key_path_resolves_to_local_ssh_dir(saage_home):
    # a credentials file pulled from another machine references key paths that
    # don't exist here — the same-named key under ~/.saage/ssh/ must win
    (saage_home / "ssh").mkdir()
    (saage_home / "ssh" / "thunder_k1").write_text("KEY")
    add_target("t1", "h", key="/home/elsewhere/.saage/ssh/thunder_k1")
    assert get_target("t1").key == saage_home / "ssh" / "thunder_k1"


def test_existing_key_path_is_used_verbatim(saage_home, tmp_path):
    real = tmp_path / "mykey"
    real.write_text("KEY")
    add_target("t2", "h", key=str(real))
    assert get_target("t2").key == real


def test_bad_target_name_rejected(saage_home):
    with pytest.raises(CredsError, match="invalid target name"):
        add_target("bad name", "h")


def test_key_path_with_quote_rejected(saage_home):
    # written as a TOML literal string — a quote would corrupt the whole file
    with pytest.raises(CredsError, match="single quote"):
        add_target("t", "h", key="C:\\Users\\o'brien\\.saage\\ssh\\k")


def test_windows_key_path_resolves_on_posix_too(saage_home):
    # backslash separators must not defeat the basename fallback
    (saage_home / "ssh").mkdir()
    (saage_home / "ssh" / "k2").write_text("KEY")
    add_target("t3", "h", key="C:\\Users\\cpadw\\.saage\\ssh\\k2")
    assert get_target("t3").key == saage_home / "ssh" / "k2"


def test_multiple_targets(saage_home):
    add_target("a", "h1")
    add_target("b", "h2", user="u", port=2222)
    targets = list_targets()
    assert set(targets) == {"a", "b"}
    assert targets["b"].port == 2222


def test_remove_target_middle_section(saage_home):
    add_target("a", "h1")
    add_target("b", "h2", user="u", port=2222)
    add_target("c", "h3")
    remove_target("b")
    assert set(list_targets()) == {"a", "c"}
    assert get_target("c").host == "h3"


def test_remove_target_last_section(saage_home):
    add_target("a", "h1")
    add_target("b", "h2")
    remove_target("b")
    assert set(list_targets()) == {"a"}


def test_remove_target_unknown_name(saage_home):
    add_target("a", "h1")
    with pytest.raises(CredsError, match="unknown target"):
        remove_target("nope")


def test_remove_target_preserves_rest_of_file(saage_home):
    # non-target content (comments, [storage], [lambda]) must survive removal
    # byte-for-byte — a TOML re-emit would strip comments
    add_target("a", "h1")
    path = cred_path()
    path.write_text("# keep this comment\n[storage]\nbucket = \"b\"\n"
                    + path.read_text())
    add_target("b", "h2")
    remove_target("a")
    text = path.read_text()
    assert "# keep this comment" in text
    assert "[storage]" in text
    assert "[targets.a]" not in text
    assert set(list_targets()) == {"b"}


def test_remove_target_keeps_key_file(saage_home):
    # per-target keys (Thunder) are unrecoverable — removal must not touch them
    (saage_home / "ssh").mkdir()
    key = saage_home / "ssh" / "thunder_k1"
    key.write_text("KEY")
    add_target("t1", "h", key=str(key))
    remove_target("t1")
    assert key.exists()


@_posix_only
def test_remove_target_keeps_0600(saage_home):
    add_target("a", "h1")
    add_target("b", "h2")
    remove_target("a")
    assert (cred_path().stat().st_mode & 0o077) == 0
