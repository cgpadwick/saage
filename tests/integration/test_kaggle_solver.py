"""Kaggle solver M0 — the full pipeline against a fake competition, offline.

Everything deterministic is REAL: prepare_comp staging, git setup, the pytest
smoke check (E2 command ACTION), the train.py contract (a scripted train.py
actually runs and writes eval_results.json), keep_or_revert with git
commit/revert, validate_submission, and the tolerant grade step (mlebench
absent -> MEDAL=unknown). Only the LLM turns are scripted.

Story: baseline scores 0.75; experiment 1 raises it to 0.80 which meets the
target -> hillclimb exits via exit_when after one iteration -> final train ->
submission validates -> report.
"""
import json
import subprocess

from saage_testkit import RoutedProvider, call, resp

from saage.hydrate import run_flow

TRAIN_V1 = """\
import json
json.dump({"metric_name": "accuracy", "value": 0.75}, open("eval_results.json", "w"))
print("epoch 1/1 train_acc=0.80 val_acc=0.75")
"""

TRAIN_V2 = """\
import json
json.dump({"metric_name": "accuracy", "value": 0.80}, open("eval_results.json", "w"))
print("epoch 1/1 train_acc=0.85 val_acc=0.80")
"""

SMOKE = "def test_ok():\n    assert True\n"

SUBMISSION = "id,target\n1,0\n2,1\n"


def _fake_competition(tmp_path):
    public = tmp_path / "mlebench" / "fake-comp" / "prepared" / "public"
    public.mkdir(parents=True)
    (tmp_path / "mlebench" / "fake-comp" / "description.md").write_text(
        "Predict target from feat. Metric: accuracy (higher is better).")
    (public / "train.csv").write_text("id,feat,target\n1,0.5,0\n2,0.7,1\n")
    (public / "sample_submission.csv").write_text("id,target\n1,0\n2,0\n")
    return tmp_path / "mlebench"


def test_kaggle_solver_pipeline(flow_copy, tmp_path):
    flow_yaml = flow_copy("kaggle_solver")
    data_root = _fake_competition(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()

    provider = RoutedProvider({
        "comp_understanding": [
            resp(calls=[call("write_file", path="competition_understanding.md",
                             content="accuracy, higher is better; tabular")]),
            resp("analysis done"),
        ],
        "comp_understanding_critic": [resp("ACTION: pass")],
        "eda": [
            resp(calls=[call("write_file", path="data_analysis.md",
                             content="2 rows, balanced target")]),
            resp("eda done"),
        ],
        "eda_critic": [resp("ACTION: pass")],
        "build_baseline": [
            resp(calls=[call("write_file", path="model.py", content="MODEL = 'lr'\n")]),
            resp(calls=[call("write_file", path="train.py", content=TRAIN_V1)]),
            resp(calls=[call("write_file", path="predict.py", content="print('noop')\n")]),
            resp(calls=[call("write_file", path="tests/test_smoke.py", content=SMOKE)]),
            resp("baseline: logistic regression"),
        ],
        # consumed in order: baseline_verify, verify_train (exp 1), final_verify
        "verify_training": [resp("ACTION: pass")] * 3,
        "propose": [resp("HYPOTHESIS: better feature helps.\n"
                         "CHANGE: train.py, improve encoding.\nRATIONALE: EDA.")],
        "proposal_critic": [resp("ACTION: pass")],
        "implement_experiment": [
            resp(calls=[call("write_file", path="train.py", content=TRAIN_V2)]),
            resp("implemented the change"),
        ],
        "make_submission": [
            resp(calls=[call("write_file", path="submission.csv", content=SUBMISSION)]),
            resp("submission written"),
        ],
        "report_narrative": [resp("The run story.")],
    })

    shared = run_flow(flow_yaml, provider=provider, workspace=ws, shared={
        "competition_id": "fake-comp",
        "mlebench_data_dir": str(data_root),
        "target_score": "0.8",
        "device": "cpu",
        "device_override": "cpu",   # hermetic: don't probe the host for a GPU
    })

    # captures flowed end to end
    assert shared["sample_submission_cols"] == "id,target"
    assert shared["sample_submission_rows"] == 2
    assert shared["best_score"] == 0.8
    assert shared["target_met"] == 1
    assert shared["final_score"] == 0.8
    assert shared["medal"] == "unknown"          # mlebench not installed; step tolerant

    # the hillclimb exited because the target was met, after exactly one experiment
    assert shared["_iter"]["hillclimb"] == 1
    assert shared["_exit_reason"]["hillclimb"] == "exit_when"

    # both deterministic E2 checks actually ran and passed (real pytest, real validator)
    assert shared["results"]["baseline_smoke"]["exit"] == 0
    assert "ACTION: pass" in shared["results"]["validate_submission"]["stdout"]

    # real artifacts in the workspace
    assert (ws / "submission.csv").read_text() == SUBMISSION
    ledger = [json.loads(line) for line in
              (ws / "experiments.jsonl").read_text().splitlines()]
    assert len(ledger) == 2                       # baseline + experiment 1
    assert ledger[0]["candidate"] == 0.75 and ledger[0]["kept"] is True
    assert ledger[1]["candidate"] == 0.8 and ledger[1]["kept"] is True

    # git history: setup snapshot, baseline keep, experiment keep, final submission
    log = subprocess.run(["git", "-C", str(ws), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    for needle in ("setup snapshot", "baseline score 0.75",
                   "keep score 0.8", "final submission"):
        assert needle in log, f"missing commit {needle!r} in:\n{log}"


def test_failed_experiment_reverts_and_counts(flow_copy, tmp_path):
    """A worse candidate must revert (code restored) and bump the failure
    counter; the loop then exits via max consecutive failures."""
    flow_yaml = flow_copy("kaggle_solver")
    data_root = _fake_competition(tmp_path)
    ws = tmp_path / "ws2"
    ws.mkdir()

    train_worse = TRAIN_V1.replace("0.75", "0.70")
    provider = RoutedProvider({
        "comp_understanding": [
            resp(calls=[call("write_file", path="competition_understanding.md",
                             content="doc")]), resp("done")],
        "comp_understanding_critic": [resp("ACTION: pass")],
        "eda": [resp(calls=[call("write_file", path="data_analysis.md",
                                 content="doc")]), resp("done")],
        "eda_critic": [resp("ACTION: pass")],
        "build_baseline": [
            resp(calls=[call("write_file", path="model.py", content="M=1\n")]),
            resp(calls=[call("write_file", path="train.py", content=TRAIN_V1)]),
            resp(calls=[call("write_file", path="predict.py", content="pass\n")]),
            resp(calls=[call("write_file", path="tests/test_smoke.py", content=SMOKE)]),
            resp("baseline"),
        ],
        "verify_training": [resp("ACTION: pass")] * 3,   # baseline, exp1, final
        "propose": [resp("HYPOTHESIS: x CHANGE: y RATIONALE: z")],
        "proposal_critic": [resp("ACTION: pass")],
        "implement_experiment": [
            resp(calls=[call("write_file", path="train.py", content=train_worse)]),
            resp("worse change"),
        ],
        "make_submission": [
            resp(calls=[call("write_file", path="submission.csv", content=SUBMISSION)]),
            resp("done"),
        ],
        "report_narrative": [resp("story")],
    })

    shared = run_flow(flow_yaml, provider=provider, workspace=ws, shared={
        "competition_id": "fake-comp",
        "mlebench_data_dir": str(data_root),
        "max_consecutive_failures": 1,            # exit after the one failure
        "device": "cpu",
        "device_override": "cpu",   # hermetic: don't probe the host for a GPU
    })

    assert shared["best_score"] == 0.75           # the worse 0.70 did not win
    assert shared["consecutive_failures"] == 1
    assert shared["_exit_reason"]["hillclimb"] == "exit_when"
    # the revert restored the baseline train.py (git checkout undid v-worse)
    assert "0.75" in (ws / "train.py").read_text()
    ledger = [json.loads(line) for line in
              (ws / "experiments.jsonl").read_text().splitlines()]
    assert ledger[-1]["kept"] is False and ledger[-1]["candidate"] == 0.7
