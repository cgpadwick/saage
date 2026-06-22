"""Skill loading: frontmatter parsing + warnings on malformed skill files."""
import logging

import pytest

from saage.skills import load_skills, parse_skill


def _skill(tmp_path, name, content):
    d = tmp_path / name
    d.mkdir()
    (d / "skill.md").write_text(content)
    return d / "skill.md"


def test_valid_frontmatter(tmp_path):
    md = _skill(tmp_path, "review",
                "---\nname: review\ndescription: check it\ntools: [read_file]\n---\nDo the review.\n")
    s = parse_skill(md)
    assert s.name == "review"
    assert s.description == "check it"
    assert s.tools == ["read_file"]
    assert s.system == "Do the review."


def test_tools_as_bare_string_raises_clear_error(tmp_path):
    # `tools: read_file` (a str, not a list) would become a set of characters
    # downstream — fail clearly at parse time instead
    md = _skill(tmp_path, "bad", "---\nname: bad\ntools: read_file\n---\nbody\n")
    with pytest.raises(ValueError, match="must be a YAML list"):
        parse_skill(md)


def test_tools_as_mapping_raises_clear_error(tmp_path):
    md = _skill(tmp_path, "bad2", "---\nname: bad2\ntools:\n  read_file: true\n---\nbody\n")
    with pytest.raises(ValueError, match="must be a YAML list"):
        parse_skill(md)


def test_tools_omitted_is_none(tmp_path):
    md = _skill(tmp_path, "ok", "---\nname: ok\ndescription: d\n---\nbody\n")
    assert parse_skill(md).tools is None


def test_no_frontmatter_uses_defaults_silently(tmp_path, caplog):
    md = _skill(tmp_path, "plain", "just instructions, no frontmatter\n")
    with caplog.at_level(logging.WARNING):
        s = parse_skill(md)
    assert s.name == "plain"                         # defaults to the directory name
    assert s.system == "just instructions, no frontmatter"
    assert caplog.records == []                      # not malformed -> no warning


def test_empty_frontmatter_is_silent(tmp_path, caplog):
    md = _skill(tmp_path, "empty", "---\n---\nbody only\n")
    with caplog.at_level(logging.WARNING):
        s = parse_skill(md)
    assert s.name == "empty"
    assert caplog.records == []                      # empty frontmatter is allowed


def test_unclosed_frontmatter_warns(tmp_path, caplog):
    md = _skill(tmp_path, "broken", "---\nname: x\nno closing fence here\n")
    with caplog.at_level(logging.WARNING):
        s = parse_skill(md)
    assert s.name == "broken"                        # fell back to defaults
    assert any("not closed" in r.message for r in caplog.records)


def test_non_mapping_frontmatter_warns(tmp_path, caplog):
    md = _skill(tmp_path, "listy", "---\n- a\n- b\n---\nbody\n")
    with caplog.at_level(logging.WARNING):
        s = parse_skill(md)
    assert s.name == "listy"
    assert any("expected a mapping" in r.message for r in caplog.records)


def test_load_skills_scans_directories(tmp_path):
    _skill(tmp_path, "a", "---\nname: a\n---\nA")
    _skill(tmp_path, "b", "---\nname: b\n---\nB")
    (tmp_path / "not_a_skill").mkdir()               # no skill.md -> ignored
    skills = load_skills(tmp_path)
    assert set(skills) == {"a", "b"}
