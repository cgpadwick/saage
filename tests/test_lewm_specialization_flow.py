"""Structural checks on the specialization flow.yaml."""
from pathlib import Path
import yaml

_FLOW = Path(__file__).resolve().parent.parent / "contrib/lewm_hillclimb_guided/flow.yaml"


def _load():
    return yaml.safe_load(_FLOW.read_text())


def test_new_shared_vars_present():
    shared = _load()["shared"]
    assert shared["test_num_eval"] == 200
    assert shared["paper_test_score"] == -1.0
    assert shared["paper_name"] == "lewm_cube_paper"
    assert shared["specialization_gain"] == -999.0


def test_paper_headline_and_gain_steps_present_and_ordered():
    ids = [s["id"] for s in _load()["workflow"]]
    for sid in ("paper_headline_clean", "paper_headline_train",
                "paper_headline_eval", "gain_record"):
        assert sid in ids, f"{sid} missing"
    # paper headline runs before the hill-climb (pristine tree); gain after confirm
    assert ids.index("paper_headline_eval") < ids.index("hillclimb")
    assert ids.index("paper_headline_eval") < ids.index("baseline_clean")
    assert ids.index("gain_record") > ids.index("confirm_eval")


def test_headline_evals_use_test_split_and_heavy_num_eval():
    steps = {s["id"]: s for s in _load()["workflow"]}
    for sid in ("paper_headline_eval", "confirm_eval"):
        run = steps[sid]["run"]
        assert "split=test" in run, f"{sid} must eval split=test"
        assert "eval.num_eval={{ test_num_eval }}" in run, f"{sid} must use test_num_eval"


def test_loop_selection_stays_on_val():
    # the in-loop eval and baseline_eval keep split=val (cheap selection)
    flat = _FLOW.read_text()
    assert "split=val" in flat
