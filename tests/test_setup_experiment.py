"""lewm setup_experiment: the --branch parameter (remote handoffs pass the run
branch so kept-experiment commits land on the branch the node pushes back)."""
import subprocess
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent
          / "contrib" / "lewm_hillclimb" / "setup_experiment.py")


def _lewm_repo(tmp_path: Path) -> Path:
    """A minimal repo that passes setup_experiment's le-wm sanity check."""
    repo = tmp_path / "lewm"
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", *args], check=True,
                       capture_output=True)
    git("init", "-q", "-b", "main")
    (repo / "train.py").write_text("# train\n")
    (repo / "jepa.py").write_text("# jepa\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return repo


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--train-epochs", "8", "--target", "74",
         *extra],
        cwd=repo, capture_output=True, text=True)


def _current_branch(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref",
                           "HEAD"], capture_output=True, text=True).stdout.strip()


def test_branch_param_creates_and_reports_the_branch(tmp_path):
    repo = _lewm_repo(tmp_path)
    proc = _run(repo, "--branch", "saage-run-lewm-x1")
    assert "SETUP=ok BRANCH=saage-run-lewm-x1" in proc.stdout, proc.stderr
    assert _current_branch(repo) == "saage-run-lewm-x1"


def test_branch_defaults_to_saage_hillclimb(tmp_path):
    repo = _lewm_repo(tmp_path)
    proc = _run(repo)
    assert "SETUP=ok BRANCH=saage-hillclimb" in proc.stdout, proc.stderr
    assert _current_branch(repo) == "saage-hillclimb"


def test_empty_branch_arg_falls_back_to_default(tmp_path):
    # a flow templating an unset {{ run_branch }} renders "" — must not
    # try to checkout an empty branch name
    repo = _lewm_repo(tmp_path)
    proc = _run(repo, "--branch", "")
    assert "SETUP=ok BRANCH=saage-hillclimb" in proc.stdout, proc.stderr


def test_already_on_the_branch_is_a_noop_switch(tmp_path):
    # the node-side clone checks out the run branch already; setup must not
    # try to re-create it
    repo = _lewm_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "saage-run-z9"],
                   check=True, capture_output=True)
    proc = _run(repo, "--branch", "saage-run-z9")
    assert "SETUP=ok BRANCH=saage-run-z9" in proc.stdout, proc.stderr
    assert _current_branch(repo) == "saage-run-z9"
