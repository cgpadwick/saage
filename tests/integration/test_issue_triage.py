"""issue_triage end to end: real engine, real commands, a tiny buggy project
planted in the workspace; gh is swapped for a canned issue JSON and only the
LLM turns are scripted. The repro is executed BY THE ENGINE (run_repro
command step) with the exact production invocation, and the final asserts
prove the repro contract mechanically: fails on the buggy tree, passes once
the bug is fixed."""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from saage.hydrate import run_flow
from saage_testkit import RoutedProvider, call, resp, tool_turn

BUGGY = "def add(a, b):\n    return a - b   # the bug\n"
FIXED = "def add(a, b):\n    return a + b\n"

ISSUE = {"number": 7, "title": "add() returns wrong result",
         "body": "mathy.add(2, 3) returns -1, expected 5.", "comments": []}

NOTES = """# Issue 7: add() returns wrong result
- expected add(2, 3) == 5, got -1
- suspect: mathy.py:2 — `return a - b` should be `return a + b`
"""

# the not-installed-package pattern the write_repro skill prescribes: put the
# script's own directory back on sys.path (PYTHONSAFEPATH removes it)
REPRO = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mathy import add

got = add(2, 3)
assert got == 5, f"expected 5, got {got}"
print("ok")
"""

REPORT = """<!doctype html><html><head><meta charset="utf-8">
<title>Triage: issue 7</title><style>body{font-family:sans-serif}</style></head>
<body>
<table>
<tr><th>Issue</th><td>#7 add() returns wrong result</td></tr>
<tr><th>The bug</th><td>add(2, 3) returns -1 instead of 5</td></tr>
<tr><th>Reproduced</th><td>✅ Verified</td></tr>
<tr><th>Root cause</th><td>mathy.py:2 subtracts instead of adding</td></tr>
<tr><th>Suggested fix</th><td>use + instead of -</td></tr>
<tr><th>Effort</th><td>XS — one-line fix</td></tr>
<tr><th>Confidence</th><td>High</td></tr>
</table>
<h2>Issue</h2><p>#7: add() returns wrong result — expected 5, got -1.</p>
<h2>Root cause</h2><p>mathy.py:2 returns a - b.</p>
<h2>Reproduction</h2><p>Verified: repro exited non-zero with the reported
wrong value.</p>
<h2>Suggested fix</h2><p>UNVALIDATED — proposed from analysis:</p>
<pre>-    return a - b   # the bug
+    return a + b</pre>
<h2>Risks / notes</h2><p>None — trivial arithmetic fix.</p>
</body></html>
"""

PY = sys.executable


def test_issue_triage_flow(flow_copy):
    flow_yaml = flow_copy("issue_triage")
    ws = flow_yaml.parent

    # plant the tiny buggy project + the canned issue in the workspace
    (ws / "mathy.py").write_text(BUGGY, encoding="utf-8")
    (ws / "canned_issue.json").write_text(json.dumps(ISSUE), encoding="utf-8")

    # offline: fetch_issue copies the canned JSON instead of calling gh
    spec = yaml.safe_load(flow_yaml.read_text(encoding="utf-8"))
    spec["workflow"][0]["run"] = "cp canned_issue.json triage_issue.json"
    flow_yaml.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    provider = RoutedProvider({
        "locate": [
            resp(calls=[call("read_file", path="triage_issue.json")]),
            resp(calls=[call("run_command", command="grep -n 'def add' mathy.py")]),
            resp(calls=[call("write_file", path="triage_notes.md", content=NOTES)]),
            resp("done"),
        ],
        "write_repro": [
            resp(calls=[call("write_file", path="repro_saage.py", content=REPRO)]),
            resp("repro written; the engine runs it next"),
        ],
        # read-only judge: the engine already ran the repro (run_repro step)
        "verify_repro": [
            resp(calls=[call("read_file", path="triage_repro_out.txt")]),
            resp("exits non-zero with the reported symptom\nACTION: pass"),
        ],
        "report": tool_turn("write_file", path="triage_report.html",
                            content=REPORT),
        "check_report": [resp("ACTION: pass")],
    })
    shared = run_flow(flow_yaml, provider=provider, shared={"issue": "7"})

    assert shared["_trace"] == ["fetch_issue", "locate", "write_repro",
                                "run_repro", "verify_repro", "report",
                                "check_report"]
    # the ENGINE recorded the repro outcome — not a model's claim
    assert shared["repro_exit"] == 1
    out = (ws / "triage_repro_out.txt").read_text(encoding="utf-8")
    assert "expected 5, got -1" in out
    assert json.loads((ws / "triage_issue.json").read_text())["number"] == 7
    html = (ws / "triage_report.html").read_text(encoding="utf-8")
    assert html == REPORT
    # the report fixture satisfies the gate's contract it is approved against
    for required in ("Reproduced", "Effort", "Confidence", "Root cause",
                     "UNVALIDATED", "✅"):
        assert required in html
    assert (ws / "mathy.py").read_text(encoding="utf-8") == BUGGY  # untouched

    # the repro contract, checked with the PRODUCTION invocation
    # (PYTHONSAFEPATH=1, like the run_repro step): non-zero on the buggy tree…
    env = {**os.environ, "PYTHONSAFEPATH": "1"}
    r = subprocess.run([PY, "repro_saage.py"], cwd=ws, capture_output=True,
                       text=True, env=env)
    assert r.returncode != 0 and "expected 5, got -1" in r.stderr
    # …and zero once the suggested fix is applied
    (ws / "mathy.py").write_text(FIXED, encoding="utf-8")
    r = subprocess.run([PY, "repro_saage.py"], cwd=ws, capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0
