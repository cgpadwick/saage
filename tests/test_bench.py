"""kaggle_solver bench.py — the pure pieces (journal, table, parsers). Offline."""
import importlib.util
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "kaggle_bench",
    Path(__file__).resolve().parent.parent / "flows" / "kaggle_solver" / "bench.py")
bench = importlib.util.module_from_spec(_spec)
sys.modules["kaggle_bench"] = bench   # dataclasses (py3.10) needs the module registered
_spec.loader.exec_module(bench)


SUMMARY = """\
14:40:03  run complete

── run summary ─────────────────────────────────
  steps:  prepare, setup
  tokens: 14,363,807 (13,076,391 in + 1,287,416 out) over 1,137 model call(s)
  cost:   ~$4.9468 (estimated)
────────────────────────────────────────────────
"""


def test_parse_run_summary():
    out = bench.parse_run_summary(SUMMARY)
    assert out == {"tokens": "14,363,807", "llm_cost_usd": "4.9468"}


def test_parse_run_summary_absent():
    assert bench.parse_run_summary("no summary here") == {}


def test_result_from_checkpoint_maps_grade_fields():
    shared = {"competition_id": "spooky-author-identification",
              "medal": "silver", "best_score": 0.32, "test_score": 0.31,
              "above_median": "true"}
    r = bench.result_from_checkpoint("run-1", shared)
    assert r.medal == "silver"
    assert r.competition == "spooky-author-identification"
    assert r.val_score == "0.32" and r.test_score == "0.31"


def test_result_from_checkpoint_defaults():
    r = bench.result_from_checkpoint("run-1", {"medal": ""})
    assert r.medal == "unknown" and r.test_score == "?"


def test_upsert_journal_replaces_by_run_id(tmp_path):
    j = tmp_path / "journal.jsonl"
    bench.upsert_journal(j, bench.RunResult(run_id="a", competition="c1"))
    bench.upsert_journal(j, bench.RunResult(run_id="b", competition="c2"))
    rows = bench.upsert_journal(
        j, bench.RunResult(run_id="a", competition="c1", medal="gold"))
    assert [r["run_id"] for r in rows] == ["b", "a"]      # replaced, not duped
    assert rows[-1]["medal"] == "gold"
    on_disk = [json.loads(l) for l in j.read_text().splitlines()]
    assert on_disk == rows


def test_render_table_has_row_per_run():
    rows = [{"run_id": "r1", "competition": "spooky", "medal": "silver",
             "date": "2026-07-03", "model": "openrouter", "val_score": "0.32",
             "test_score": "0.31", "above_median": "true",
             "llm_cost_usd": "4.95", "gpu_hours": "10.2"}]
    md = bench.render_table(rows)
    assert "| 2026-07-03 | spooky | openrouter | silver | true | 0.32 " in md
    assert md.count("`r1`") == 1


def test_gpu_hours():
    assert bench.gpu_hours("2026-07-02T04:26:04Z", "2026-07-02T14:40:03Z") == "10.2"
    assert bench.gpu_hours("?", "?") == "?"


def test_lower_is_better_covers_launch_set():
    for comp in ("spooky-author-identification",
                 "nomad2018-predict-transparent-conductors"):
        assert comp in bench.LOWER_IS_BETTER


def test_memory_note_has_outcome_and_log():
    rec = bench.RunResult(run_id="r1", competition="spooky", medal="silver",
                          val_score="0.32", test_score="0.31",
                          above_median="true", llm_cost_usd="4.95")
    note = bench.memory_note(rec, "## Experiment 1 — KEPT\ntfidf helped\n")
    assert "# spooky — run r1" in note
    assert "medal=silver" in note and "tfidf helped" in note


def test_memory_note_truncates_huge_logs():
    rec = bench.RunResult(run_id="r", competition="c")
    note = bench.memory_note(rec, "x" * 20000)
    assert len(note) < 9000 and "truncated" in note
