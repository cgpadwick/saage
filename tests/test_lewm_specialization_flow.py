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
    # selection vs headline use DIFFERENT eval seeds (saage-only held-out test)
    assert shared["val_seed"] == 42
    assert shared["test_seed"] == 1234
    assert shared["val_seed"] != shared["test_seed"]


def test_paper_headline_and_gain_steps_present_and_ordered():
    ids = [s["id"] for s in _load()["workflow"]]
    for sid in ("paper_headline_clean", "paper_headline_train",
                "paper_headline_eval", "gain_record"):
        assert sid in ids, f"{sid} missing"
    # paper headline runs before the hill-climb (pristine tree); gain after confirm
    assert ids.index("paper_headline_eval") < ids.index("hillclimb")
    assert ids.index("paper_headline_eval") < ids.index("baseline_clean")
    assert ids.index("gain_record") > ids.index("confirm_eval")


def test_headline_evals_use_test_seed_and_heavy_num_eval():
    steps = {s["id"]: s for s in _load()["workflow"]}
    for sid in ("paper_headline_eval", "confirm_eval"):
        run = steps[sid]["run"]
        assert "seed={{ test_seed }}" in run, f"{sid} must eval with the test seed"
        assert "eval.num_eval={{ test_num_eval }}" in run, f"{sid} must use test_num_eval"
        assert "seed={{ val_seed }}" not in run, f"{sid} must not use the val seed"


def test_loop_selection_uses_val_seed():
    # baseline_eval and the in-loop eval evaluate with the val seed (cheap selection);
    # the held-out test seed never appears on a selection eval
    steps = {s["id"]: s for s in _load()["workflow"]}
    baseline = steps["baseline_eval"]["run"]
    assert "seed={{ val_seed }}" in baseline
    assert "seed={{ test_seed }}" not in baseline
    # no split= keyword anywhere (stock eval.py has no split; we use seeds)
    assert "split=" not in _FLOW.read_text()
