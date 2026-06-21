from saage.remote.observe import reconcile


def _run(run_id, phase, target="spark"):
    return {"run_id": run_id, "phase": phase, "target": target,
            "tmux_session": f"saage-{run_id}"}


def test_running_run_with_live_session_is_clean():
    rows = reconcile([_run("r1", "running")], {"spark": ["saage-r1"]})
    assert rows == [{"run_id": "r1", "phase": "running", "target": "spark",
                     "alive": True, "note": ""}]


def test_running_run_with_no_session_is_flagged():
    rows = reconcile([_run("r1", "running")], {"spark": []})
    assert rows[0]["alive"] is False
    assert "no session" in rows[0]["note"]


def test_final_run_with_live_session_is_flagged():
    rows = reconcile([_run("r1", "done")], {"spark": ["saage-r1"]})
    assert "still alive" in rows[0]["note"]


def test_orphan_session_is_flagged():
    rows = reconcile([], {"spark": ["saage-mystery"]})
    assert len(rows) == 1
    assert "ORPHAN" in rows[0]["note"]
    assert rows[0]["target"] == "spark"


def test_orphan_detection_is_per_target():
    # same session name on another target is NOT claimed by spark's run
    rows = reconcile([_run("r1", "running", target="spark")],
                     {"spark": ["saage-r1"], "lam1": ["saage-r1"]})
    orphans = [r for r in rows if "ORPHAN" in r["note"]]
    assert len(orphans) == 1
    assert orphans[0]["target"] == "lam1"


def test_done_run_with_no_session_is_quiet():
    rows = reconcile([_run("r1", "done")], {"spark": []})
    assert rows[0]["note"] == ""


def test_orphan_warning_suggests_resume(capsys):
    # reuse the module's existing orphan path; assert the hint text
    import saage.remote.observe as observe
    assert "saage remote resume" in observe._ORPHAN_HINT
