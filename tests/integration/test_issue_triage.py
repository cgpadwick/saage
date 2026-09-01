"""issue_triage end to end: real engine, real commands, a tiny buggy project
planted in the workspace; gh is swapped for a canned issue JSON and only the
LLM turns are scripted. The final asserts prove the repro contract
mechanically: fails on the buggy tree, passes once the bug is fixed."""
import json
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

REPRO = """import sys
from mathy import add

got = add(2, 3)
assert got == 5, f"expected 5, got {got}"
print("ok")
"""

REPORT = """# Triage: issue 7
Root cause: mathy.py:2 subtracts instead of adding. Repro verified.
```diff
-    return a - b   # the bug
+    return a + b
```
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
            resp(calls=[call("run_command", command=f"'{PY}' repro_saage.py")]),
            resp("repro fails as the issue reports"),
        ],
        "verify_repro": [
            resp(calls=[call("run_command", command=f"'{PY}' repro_saage.py")]),
            resp("exits non-zero with the reported symptom\nACTION: pass"),
        ],
        "report": tool_turn("write_file", path="triage_report.md",
                            content=REPORT),
    })
    shared = run_flow(flow_yaml, provider=provider, shared={"issue": "7"})

    assert shared["_trace"] == ["fetch_issue", "locate", "write_repro",
                                "verify_repro", "report"]
    assert json.loads((ws / "triage_issue.json").read_text())["number"] == 7
    assert (ws / "triage_report.md").read_text(encoding="utf-8") == REPORT
    assert (ws / "mathy.py").read_text(encoding="utf-8") == BUGGY  # untouched

    # the repro contract, checked mechanically: non-zero on the buggy tree...
    r = subprocess.run([PY, "repro_saage.py"], cwd=ws, capture_output=True,
                       text=True)
    assert r.returncode != 0 and "expected 5, got -1" in r.stderr
    # ...and zero once the suggested fix is applied
    (ws / "mathy.py").write_text(FIXED, encoding="utf-8")
    r = subprocess.run([PY, "repro_saage.py"], cwd=ws, capture_output=True,
                       text=True)
    assert r.returncode == 0
