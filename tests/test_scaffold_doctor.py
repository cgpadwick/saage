"""saage new (template scaffold) and saage doctor (setup checks)."""
import pytest

from saage.cli import main
from saage.hydrate import build_flow
from saage.scaffold import new_flow


class TestNew:
    def test_scaffold_hydrates_out_of_the_box(self, tmp_path):
        dest = new_flow("my_flow", tmp_path)
        assert (dest / "flow.yaml").is_file()
        assert (dest / "do_work" / "skill.md").is_file()
        # the template must pass the same check `saage validate` runs
        build_flow(dest / "flow.yaml", provider=object(),
                   workspace=str(tmp_path / "ws"))

    def test_scaffold_prefers_flows_dir(self, tmp_path, monkeypatch):
        (tmp_path / "flows").mkdir()
        monkeypatch.chdir(tmp_path)
        assert new_flow("f").resolve() == (tmp_path / "flows" / "f").resolve()

    def test_refuses_to_overwrite(self, tmp_path):
        new_flow("f", tmp_path)
        with pytest.raises(FileExistsError, match="already exists"):
            new_flow("f", tmp_path)

    def test_cli_prints_next_steps(self, tmp_path, capsys):
        assert main(["new", "cli_flow", "--dir", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "saage validate" in out and "saage run" in out

    def test_cli_existing_dir_is_clean_error(self, tmp_path, capsys):
        main(["new", "dup", "--dir", str(tmp_path)])
        assert main(["new", "dup", "--dir", str(tmp_path)]) == 1
        assert "already exists" in capsys.readouterr().err


class TestDoctor:
    def test_doctor_runs_and_reports(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "OPENROUTER_API_KEY", "NVIDIA_API_KEY"):
            monkeypatch.delenv(env, raising=False)
        assert main(["doctor"]) == 0          # warnings only, no hard problems
        out = capsys.readouterr().out
        assert "python" in out
        assert "no provider key" in out
        assert "saage new" in out             # points at the scaffold

    def test_doctor_flags_broken_flow(self, tmp_path, monkeypatch, capsys):
        d = tmp_path / "flows" / "bad"
        d.mkdir(parents=True)
        (d / "flow.yaml").write_text("workflow:\n  - {id: a, type: wat}\n")
        monkeypatch.chdir(tmp_path)
        assert main(["doctor"]) == 1
        out = capsys.readouterr().out
        assert "bad" in out and "problem" in out

    def test_doctor_validates_good_flow(self, tmp_path, monkeypatch, capsys):
        new_flow("good", tmp_path / "flows")
        monkeypatch.chdir(tmp_path)
        assert main(["doctor"]) == 0
        assert "good: hydrates" in capsys.readouterr().out
