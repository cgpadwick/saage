"""Structural checks on the held-out-test wiring of the lewm_hillclimb_guided flow.

Selection (baseline + in-loop eval) runs on the val seed; the final confirm eval
runs on a DIFFERENT test seed + a heavier num_eval. No le-wm change is involved
(stock eval.py samples by `seed`), so these checks guard the seed wiring only.
"""
from pathlib import Path
import yaml

_FLOW = Path(__file__).resolve().parent.parent / "contrib/lewm_hillclimb_guided/flow.yaml"


def _load():
    return yaml.safe_load(_FLOW.read_text())


def test_holdout_seed_vars_present_and_distinct():
    shared = _load()["shared"]
    assert shared["val_seed"] == 42
    assert shared["test_seed"] == 1234
    assert shared["val_seed"] != shared["test_seed"]
    assert shared["test_num_eval"] == 200


def test_no_specialization_experiment_leftovers():
    # the paper-recipe headline / gain experiment was removed; guard against its return
    shared = _load()["shared"]
    for key in ("paper_test_score", "paper_name", "specialization_gain"):
        assert key not in shared, f"{key} should be gone"
    ids = [s["id"] for s in _load()["workflow"]]
    for sid in ("paper_headline_clean", "paper_headline_train",
                "paper_headline_eval", "gain_record"):
        assert sid not in ids, f"{sid} step should be gone"


def test_confirm_eval_uses_test_seed_and_heavy_num_eval():
    steps = {s["id"]: s for s in _load()["workflow"]}
    run = steps["confirm_eval"]["run"]
    assert "seed={{ test_seed }}" in run
    assert "eval.num_eval={{ test_num_eval }}" in run
    assert "seed={{ val_seed }}" not in run


def test_selection_evals_use_val_seed():
    steps = {s["id"]: s for s in _load()["workflow"]}
    baseline = steps["baseline_eval"]["run"]
    assert "seed={{ val_seed }}" in baseline
    assert "seed={{ test_seed }}" not in baseline
    # the in-loop eval (nested in the hillclimb counting_loop body) must also
    # select on the val seed — never the held-out test seed
    hillclimb_body = {s["id"]: s for s in steps["hillclimb"]["body"]}
    inner_eval = hillclimb_body["eval"]["run"]
    assert "seed={{ val_seed }}" in inner_eval
    assert "seed={{ test_seed }}" not in inner_eval
    # no split= keyword anywhere (stock eval.py has no split; we use seeds)
    assert "split=" not in _FLOW.read_text()
