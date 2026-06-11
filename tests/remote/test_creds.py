import os
from pathlib import Path

import pytest

from saage.remote.creds import (CredsError, add_target, cred_path, get_target,
                                list_targets, load_creds)

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


def test_bad_target_name_rejected(saage_home):
    with pytest.raises(CredsError, match="invalid target name"):
        add_target("bad name", "h")


def test_multiple_targets(saage_home):
    add_target("a", "h1")
    add_target("b", "h2", user="u", port=2222)
    targets = list_targets()
    assert set(targets) == {"a", "b"}
    assert targets["b"].port == 2222
