# issue_triage

Triage a GitHub issue against a **working checkout**: fetch the issue,
locate the responsible code, build a reproduction the engine verifies
actually fails for the issue's stated reason, then write a report with a
suggested (never applied) fix.

The design bet: run where a human maintainer would — your dev checkout,
where imports and tests already work. No clone, no dependency install, no
environment archaeology. The `--workspace` flag points every tool and
command at the target repo.

## Requirements

- The target project checked out and importable (its venv at `.venv`, or
  pass `--venv`), with the [`gh` CLI](https://cli.github.com/) authenticated
  (`gh auth status`) — the repo is inferred from the checkout's git remote.

## Run

```bash
saage run flows/issue_triage/flow.yaml \
  --workspace ~/code/some-project \
  --set issue=1234
```

Knobs: `issue` (required), `test_cmd` (default `pytest -q`, used for risk
notes in the report).

## Output

All artifacts land in the target workspace, clearly prefixed, project files
never modified:

- `triage_issue.json` — the fetched issue
- `triage_notes.md` — located suspect code, `path:line`, hypothesis
- `repro_saage.py` — self-contained repro; exits non-zero while the bug
  exists, would pass once fixed
- `triage_report.html` — self-contained HTML: an executive summary table up
  front (the bug, reproduced ✅/❌, root cause, suggested fix, T-shirt effort
  estimate XS–XL, confidence — the 30-second read), then root cause,
  verified repro status, suggested fix as a unified diff, risks

## How it works

`fetch_issue` (command: `gh issue view --json`) → `locate` (agent greps and
reads the codebase, writes evidence) → `repro_loop` (retry_loop, max 3: an
agent writes `repro_saage.py`; a checker *runs it* and passes only if it
fails non-zero **for the issue's stated symptom** and asserts the expected
behavior — so it would pass on a fixed tree) → `report_loop` (retry_loop:
an agent writes the HTML report; a checker fails until the file exists with
the executive summary table and every section).

The repro gate is the point: a triage report backed by an executing,
symptom-matched reproduction, enforced by the engine's loop wiring rather
than agent diligence. If three attempts can't reproduce, the report says
"unreproduced" and downgrades the root cause to a hypothesis — an honest
outcome, not a silent one.

Good first targets: issues labeled `bug` with a code snippet in the body,
on pure-Python projects with fast test suites (click, rich, textual).
