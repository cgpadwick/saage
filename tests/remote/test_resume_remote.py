import pytest
from saage.remote import resume as rresume


class FakeConn:
    def __init__(self): self.calls = []
    def run(self, cmd, **kw):
        self.calls.append(cmd)
        class P: returncode = 0; stdout = ""; stderr = ""
        return P()
    def write_file(self, path, content, mode=None): self.calls.append(("write", path))
    def rsync_to(self, *a, **k): self.calls.append(("rsync", a))
    dest = "fake"


def test_resume_in_place_when_node_alive(monkeypatch, tmp_path):
    # node alive + run not actively running -> push resume.sh + launch, no r2pull
    decision = rresume.decide(node_alive=True, session_running=False, have_target=False)
    assert decision == "in_place"


def test_resume_cross_box_when_node_gone(monkeypatch):
    decision = rresume.decide(node_alive=False, session_running=False, have_target=True)
    assert decision == "cross_box"


def test_resume_refuses_running_run():
    with pytest.raises(rresume.ResumeError):
        rresume.decide(node_alive=True, session_running=True, have_target=False)


def test_resume_cross_box_needs_target():
    with pytest.raises(rresume.ResumeError):
        rresume.decide(node_alive=False, session_running=False, have_target=False)
