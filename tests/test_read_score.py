"""The greenfield score reader: only a valid [0,1] value from eval_results.json
yields a capturable SCORE= token; anything else is rejected."""
import subprocess
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent
          / "flows" / "greenfield_ml" / "read_score.py")


def _run(tmp_path, content):
    if content is not None:
        (tmp_path / "eval_results.json").write_text(content)
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()


def test_valid_score(tmp_path):
    assert _run(tmp_path, '{"metric_name": "accuracy", "value": 0.93}') == "SCORE=0.93"


def test_out_of_range_is_not_captured(tmp_path):
    out = _run(tmp_path, '{"value": 98}')          # mis-scaled (percent)
    assert "SCORE=" not in out and "OUT_OF_RANGE" in out


def test_non_numeric_is_rejected(tmp_path):
    assert "SCORE=" not in _run(tmp_path, '{"value": "oops"}')


def test_missing_key_is_rejected(tmp_path):
    assert "SCORE=" not in _run(tmp_path, '{"metric_name": "accuracy"}')


def test_missing_file_is_rejected(tmp_path):
    assert "SCORE_MISSING" in _run(tmp_path, None)


# --- key aliases: a differently-named eval output still yields the score, so a
# new model writing `accuracy`/`score` instead of `value` isn't a silent failure


def test_accuracy_alias(tmp_path):
    assert _run(tmp_path, '{"metric_name": "accuracy", "accuracy": 0.91}') == "SCORE=0.91"


def test_score_alias(tmp_path):
    assert _run(tmp_path, '{"score": 0.88}') == "SCORE=0.88"


def test_canonical_value_wins_over_alias(tmp_path):
    # prefer the canonical `value` when multiple keys are present
    assert _run(tmp_path, '{"value": 0.9, "accuracy": 0.1}') == "SCORE=0.9"
