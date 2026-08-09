"""Flow E — brownfield hill-climb over an existing repo (le-wm), offline.

The workspace is a fake le-wm repo: `train.py` fabricates the checkpoint file
the real one would save, `eval.py` replays scripted success rates (52 baseline,
80 for the first experiment). Only the LLM turns are scripted; setup,
clean/train/eval commands, git keep/revert, and checkpoint promotion all run
for real against the temp repo + a temp $STABLEWM_HOME.
"""
import subprocess
import sys
from pathlib import Path

from saage_testkit import RoutedProvider, resp, tool_turn

from saage.hydrate import run_flow

FAKE_TRAIN = """\
import os, sys
from pathlib import Path
name = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("output_model_name="))
epochs = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("trainer.max_epochs=")), "8")
d = Path(os.environ["STABLEWM_HOME"]) / "checkpoints" / name
d.mkdir(parents=True, exist_ok=True)
(d / f"weights_epoch_{epochs}.pt").write_text("weights")
print("trained", name)
"""

FAKE_EVAL = """\
import os
from pathlib import Path
counter = Path(os.environ["STABLEWM_HOME"]) / "eval_count"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
scores = [52.0, 80.0]   # baseline, then the first (winning) experiment
print({'success_rate': scores[min(n, len(scores) - 1)], 'episode_successes': []})
"""


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    return r.stdout


def make_fake_lewm_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "lewm_repo"
    (repo / "config" / "train").mkdir(parents=True)
    (repo / "train.py").write_text(FAKE_TRAIN)
    (repo / "eval.py").write_text(FAKE_EVAL)
    (repo / "jepa.py").write_text("# fake\n")
    (repo / "config" / "train" / "lewm.yaml").write_text("lr: 5e-5\n")
    # the flow's commands invoke `python`; the real le-wm workspace provides it
    # via its .venv (auto-activated by the engine) — mirror that here
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").symlink_to(sys.executable)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return repo


def test_lewm_hillclimb(flow_copy, tmp_path, monkeypatch):
    flow_yaml = flow_copy("lewm_hillclimb")
    repo = make_fake_lewm_repo(tmp_path)
    cache = tmp_path / "stablewm_home"
    (cache / "checkpoints").mkdir(parents=True)
    monkeypatch.setenv("STABLEWM_HOME", str(cache))

    provider = RoutedProvider({
        "propose": [resp("HYPOTHESIS: lr too low. CHANGE: config/train/lewm.yaml "
                         "lr 5e-5 -> 1e-4. RATIONALE: short budget.")],
        "proposal_critic": [resp("Specific and grounded.\n\nACTION: pass")],
        "implement": tool_turn("write_file", path="config/train/lewm.yaml",
                               content="lr: 1e-4\n"),
        "verify": [resp("Diff matches the proposal; smoke ok.\n\nACTION: pass")],
        "report_narrative": tool_turn("write_file", path="report_narrative.md",
                                      content="# Hill-climb report\n"),
    })

    shared = run_flow(flow_yaml, provider=provider, workspace=repo,
                      shared={"stablewm_home": str(cache)})

    # baseline 52 seeded best_score; experiment scored 80 and was kept
    assert shared["best_score"] == 80.0
    assert shared["consecutive_failures"] == 0
    # 80 >= target 74 -> the loop exited via exit_when after one iteration
    assert shared["_exit_reason"]["hillclimb"] == "exit_when"
    assert shared["_trace"].count("propose") == 1
    assert shared["_trace"].count("train") == 1          # hillclimb train (baseline_train is separate)
    assert shared["_trace"][-1] == "report_commit"

    # the winning change survived keep_or_revert and was committed on the branch
    assert (repo / "config" / "train" / "lewm.yaml").read_text() == "lr: 1e-4\n"
    log = git(repo, "log", "--oneline")
    assert "saage: keep experiment, success_rate 80.0" in log
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "saage-hillclimb"

    # the best checkpoint was promoted outside the repo
    assert (cache / "checkpoints" / "lewm_cube_best" / "weights_epoch_8.pt").exists()
    # the experiment ledger recorded baseline + experiment
    lines = (repo / "experiments.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
