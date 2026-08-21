"""Flow-spec validation: every authoring mistake gets a clean, addressed
message instead of a bare KeyError traceback."""
import pytest

from saage.cli import main
from saage.hydrate import build_flow
from saage.validate import FlowSpecError, validate_spec


def _err(spec, **kw) -> str:
    with pytest.raises(FlowSpecError) as ei:
        validate_spec(spec, **kw)
    return str(ei.value)


class TestValidateSpec:
    def test_empty_file(self):
        assert "empty file" in _err(None)

    def test_not_a_mapping(self):
        assert "must be a YAML mapping" in _err(["steps"])

    def test_missing_provider_and_workflow(self):
        msg = _err({})
        assert "'provider:'" in msg and "'workflow:'" in msg

    def test_provider_skipped_when_injected(self):
        validate_spec({"workflow": [{"id": "s", "type": "command", "run": "x"}]},
                      require_provider=False)

    def test_empty_workflow(self):
        assert "non-empty list" in _err({"workflow": []}, require_provider=False)

    def test_step_missing_type(self):
        assert "missing 'type'" in _err(
            {"workflow": [{"id": "a"}]}, require_provider=False)

    def test_unknown_step_type(self):
        msg = _err({"workflow": [{"id": "a", "type": "wat"}]},
                   require_provider=False)
        assert "unknown step type 'wat'" in msg and "counting_loop" in msg

    def test_step_missing_id_names_the_position(self):
        msg = _err({"workflow": [{"type": "agent", "skill": "s"}]},
                   require_provider=False)
        assert "workflow[0]" in msg and "'id'" in msg

    def test_command_missing_run(self):
        assert "'run'" in _err({"workflow": [{"id": "c", "type": "command"}]},
                               require_provider=False)

    def test_nested_errors_are_addressed(self):
        msg = _err({"workflow": [{
            "id": "loop", "type": "counting_loop",
            "body": [{"id": "inner", "type": "agent"}]}]},
            require_provider=False)
        assert "body[0]" in msg and "'skill'" in msg

    def test_retry_loop_missing_check(self):
        msg = _err({"workflow": [{
            "id": "r", "type": "retry_loop",
            "action": {"id": "a", "type": "command", "run": "x"}}]},
            require_provider=False)
        assert "'check'" in msg

    def test_polling_loop_missing_bounds(self):
        msg = _err({"workflow": [{
            "id": "p", "type": "polling_loop",
            "poll": {"id": "a", "type": "command", "run": "x"},
            "status": {"id": "b", "type": "command", "run": "y"}}]},
            require_provider=False)
        assert "interval_seconds" in msg and "max_wait_seconds" in msg

    def test_all_errors_reported_at_once(self):
        msg = _err({"workflow": [{"id": "a", "type": "command"},
                                 {"type": "agent"}]},
                   require_provider=False)
        assert "'run'" in msg and "'skill'" in msg and "'id'" in msg


class TestBuildFlowMessages:
    def test_unknown_skill_lists_available(self, tmp_path):
        (tmp_path / "real").mkdir()
        (tmp_path / "real" / "skill.md").write_text("---\n---\nSKILL_ID: real\n")
        f = tmp_path / "flow.yaml"
        f.write_text("workflow:\n  - {id: a, type: agent, skill: nope}\n")
        with pytest.raises(FlowSpecError, match="unknown skill 'nope'.*real"):
            build_flow(f, provider=object(), workspace=str(tmp_path / "ws"))

    def test_message_carries_the_file_path(self, tmp_path):
        f = tmp_path / "flow.yaml"
        f.write_text("workflow: []\n")
        with pytest.raises(FlowSpecError, match="flow.yaml"):
            build_flow(f, provider=object(), workspace=str(tmp_path / "ws"))


class TestCli:
    def _flow(self, tmp_path, text):
        f = tmp_path / "flow.yaml"
        f.write_text(text)
        return f

    def test_validate_ok(self, tmp_path, capsys):
        f = self._flow(tmp_path,
                       "provider: {type: local, model: m}\n"
                       "workflow:\n  - {id: s, type: command, run: 'echo hi'}\n")
        assert main(["validate", str(f)]) == 0
        assert "ok:" in capsys.readouterr().out

    def test_validate_bad_spec_exits_1_with_message(self, tmp_path, capsys):
        f = self._flow(tmp_path, "workflow:\n  - {id: s, type: wat}\n")
        assert main(["validate", str(f)]) == 1
        err = capsys.readouterr().err
        assert "saage: error:" in err and "unknown step type" in err
        assert "Traceback" not in err

    def test_validate_missing_file(self, tmp_path, capsys):
        assert main(["validate", str(tmp_path / "nope.yaml")]) == 1
        assert "not found" in capsys.readouterr().err

    def test_run_missing_flow_leaves_no_run_dir(self, tmp_path, capsys):
        # SAAGE_HOME is redirected to tmp by the autouse fixture
        import os
        assert main(["run", str(tmp_path / "nope.yaml"), "-q"]) == 1
        runs = os.path.join(os.environ["SAAGE_HOME"], "runs")
        assert not os.path.isdir(runs) or not os.listdir(runs)

    def test_run_bad_spec_is_clean_error(self, tmp_path, capsys):
        f = self._flow(tmp_path, "provider: {type: local, model: m}\n")
        assert main(["run", str(f), "-q"]) == 1
        err = capsys.readouterr().err
        assert "workflow" in err and "Traceback" not in err
