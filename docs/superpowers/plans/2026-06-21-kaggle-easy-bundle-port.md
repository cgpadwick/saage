# kaggle_solver Easy-Bundle Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Update — report agent added after this plan.** The HTML report (#6) was
> originally deferred here, but a `report` LLM agent was subsequently added to the
> same PR (per `2026-06-21-report-agent-design.md`): the flow's final step is now
> `report` (replacing `report_narrative`) and it collects `report.html`. So the
> implemented `artifacts:` are `[experiments.jsonl, research_log.md, report.html]`
> — the `report_narrative.md` snippet in Task 2 below is superseded.

**Goal:** Port the hill-climb ledger fixes (anchoring, terse summary research_log, a `summarize` agent, per-run reset, generated-output filtering, artifacts) from `greenfield_ml`/`lewm_hillclimb_guided` to `flows/kaggle_solver`.

**Architecture:** Mirror the just-shipped reference implementations. `keep_or_revert.py` records `commit_sha`/`parent_step`/`files_changed` + a one-paragraph summary (terse research_log) while the full proposal goes to `experiments.jsonl`; a new single-purpose `summarize` agent writes `proposals/summary.md`; `setup_competition.py` resets the ledger per run. No engine changes.

**Tech Stack:** Python 3.10+, the saage flow engine (`flow.yaml` + `skill.md`), pytest (offline, subprocess-in-temp-git-repo tests).

## Global Constraints

- Tests are offline/hermetic: run the real script as a subprocess in a throwaway git repo; no provider, no kaggle data. Run with `python -m pytest`, never bare `pytest`.
- Match the existing terse, comment-rich style; full suite must stay green (`python -m pytest -q`).
- nan-safe: `candidate`/`best` may be `nan` (failed train/eval). Anchoring is score-independent; jsonl already coerces nan→`None`. research_log formatting must not print raw `nan` floats awkwardly — render `nan` as `n/a`.
- `_LEDGER_FILES` (filtered from `files_changed`) = `{"research_log.md", "experiments.jsonl", "eval_results.json", "submission.csv", "training.log"}`; also skip any path under `proposals/` or `checkpoints/`.
- summarize wiring: a `summarize` agent step goes **between `propose_loop` and `implement_loop`** in `flows/kaggle_solver/flow.yaml`.
- Deferred (NOT in this plan): verify diff==proposal (#5), HTML report.py (#6).

---

### Task 1: Ledger anchoring + terse summary research_log in `keep_or_revert.py`

**Files:**
- Modify: `flows/kaggle_solver/keep_or_revert.py`
- Test: `tests/test_kaggle_keep_or_revert.py` (create)

**Interfaces:**
- Consumes: `proposals/latest.md` (full proposal, written by the propose agent), `proposals/summary.md` (one paragraph, written by the summarize agent in Task 2 — absent in unit tests, falls back to `"(no summary written)"`).
- Produces: `experiments.jsonl` rows `{step, parent_step, candidate, best, kept, commit_sha, files_changed, summary, proposal}`; terse `research_log.md` entries.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kaggle_keep_or_revert.py`:

```python
"""Unit tests for the kaggle_solver hill-climb keep/revert helper (offline).

Runs the real script as a subprocess in a throwaway git repo (no LLM). Mirrors
tests/test_keep_or_revert.py but for kaggle's arg shape (--baseline/--target,
nan sentinels) and its generated-output set (submission.csv, training.log).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "flows" / "kaggle_solver" / "keep_or_revert.py")


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A git repo with one committed file = the 'last kept' baseline. Mirrors
    production by gitignoring experiments.jsonl so it survives `git clean` on a
    revert (the ledger must accumulate across the run)."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "model.py").write_text("v = 1\n")
    (tmp_path / ".gitignore").write_text("experiments.jsonl\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    return tmp_path


def _run(repo, candidate, best, failures=0, lower_is_better="false",
         target="", baseline="false"):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--candidate", str(candidate),
         "--best", str(best), "--failures", str(failures),
         "--lower-is-better", lower_is_better, "--target", target,
         "--baseline", baseline],
        cwd=repo, capture_output=True, text=True, check=True)
    return dict(tok.split("=", 1) for tok in r.stdout.split() if "=" in tok)


def _last_experiment(repo):
    rows = [json.loads(l) for l in (repo / "experiments.jsonl").read_text().splitlines() if l.strip()]
    return rows[-1]


def _head_sha(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


def test_keep_records_commit_sha_parent_and_files(repo):
    (repo / "model.py").write_text("v = 2\n")
    _run(repo, candidate=0.9, best=0.8)                   # higher-is-better keep
    rec = _last_experiment(repo)
    assert rec["kept"] is True
    assert rec["commit_sha"] == _head_sha(repo)
    assert "model.py" in rec["files_changed"]
    assert rec["step"] == 1 and rec["parent_step"] == 0


def test_revert_records_files_but_null_sha(repo):
    (repo / "model.py").write_text("v = 999\n")
    (repo / "extra.py").write_text("x = 1\n")             # untracked candidate file
    _run(repo, candidate=0.7, best=0.8)                   # not improved -> revert
    rec = _last_experiment(repo)
    assert rec["kept"] is False
    assert rec["commit_sha"] is None
    assert "model.py" in rec["files_changed"] and "extra.py" in rec["files_changed"]


def test_files_changed_excludes_bookkeeping_and_outputs(repo):
    (repo / "model.py").write_text("v = 2\n")
    (repo / "research_log.md").write_text("- prior\n")
    (repo / "eval_results.json").write_text('{"value": 0.9}\n')
    (repo / "submission.csv").write_text("id,target\n1,0\n")
    (repo / "training.log").write_text("epoch 1\n")
    _run(repo, candidate=0.9, best=0.8)
    rec = _last_experiment(repo)
    for noise in ("research_log.md", "experiments.jsonl", "eval_results.json",
                  "submission.csv", "training.log"):
        assert noise not in rec["files_changed"]


def test_research_log_has_summary_full_proposal_in_jsonl(repo):
    (repo / "proposals").mkdir()
    (repo / "proposals" / "latest.md").write_text(
        "HYPOTHESIS: add TF-IDF features.\nCHANGE: ngram_range (1,1)->(1,2).\n"
        "RATIONALE: lots of detail that must NOT bloat the log.\n")
    (repo / "proposals" / "summary.md").write_text(
        "Add bigram TF-IDF features (ngram_range 1->2) to capture word pairs.")
    (repo / "model.py").write_text("v = 2\n")
    _run(repo, candidate=0.9, best=0.8)
    log = (repo / "research_log.md").read_text()
    assert "## Experiment 1" in log and "KEPT" in log
    assert "Add bigram TF-IDF features" in log
    assert "model.py" in log
    assert "RATIONALE: lots of detail" not in log
    rec = _last_experiment(repo)
    assert "RATIONALE: lots of detail" in rec["proposal"]
    assert rec["summary"] == "Add bigram TF-IDF features (ngram_range 1->2) to capture word pairs."


def test_summary_recorded_on_revert(repo):
    (repo / "proposals").mkdir()
    (repo / "proposals" / "summary.md").write_text("Try LR=0.5 (too high).")
    (repo / "model.py").write_text("v = 9\n")
    _run(repo, candidate=0.3, best=0.9)                   # revert
    log = (repo / "research_log.md").read_text()
    assert "reverted" in log and "Try LR=0.5 (too high)" in log
    assert not (repo / "proposals" / "summary.md").exists()  # git clean wiped it


def test_baseline_parent_zero_and_has_sha(repo):
    (repo / "model.py").write_text("v = 2\n")
    out = _run(repo, candidate=0.8, best="nan", baseline="true")
    assert out["RESULT"] == "keep"
    rec = _last_experiment(repo)
    assert rec["kept"] is True and rec["parent_step"] == 0
    assert rec["commit_sha"] == _head_sha(repo)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_kaggle_keep_or_revert.py -q`
Expected: FAIL — `KeyError: 'commit_sha'` / `'parent_step'` / `'files_changed'` / `'summary'` (current `_record_experiment` writes none of these), and the research_log summary assertions fail (current log is a bare `- candidate=.. -> status` line).

- [ ] **Step 3: Add the helpers to `flows/kaggle_solver/keep_or_revert.py`**

Add `git_out` next to `git` (after the `git` function, ~line 29):

```python
def git_out(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip()


# harness bookkeeping + generated outputs — not part of an experiment's code
# footprint (eval/submission/log files are produced by train/predict, not edits)
_LEDGER_FILES = {"research_log.md", "experiments.jsonl", "eval_results.json",
                 "submission.csv", "training.log"}


def _changed_files() -> list[str]:
    """The experiment's code footprint vs the last kept commit, minus bookkeeping
    and generated outputs. Captured BEFORE commit/revert so a reverted attempt
    still records what it tried."""
    tracked = git_out("diff", "--name-only", "HEAD").splitlines()
    untracked = git_out("ls-files", "--others", "--exclude-standard").splitlines()
    files = set()
    for path in tracked + untracked:
        path = path.strip()
        if (not path or path in _LEDGER_FILES
                or path.startswith("proposals/") or path.startswith("checkpoints/")):
            continue
        files.add(path)
    return sorted(files)


def _read_proposal() -> str:
    p = "proposals/latest.md"
    return open(p).read().strip() if os.path.exists(p) else ""


def _read_summary() -> str:
    """The summarize agent's one-paragraph digest of the proposal."""
    p = "proposals/summary.md"
    return open(p).read().strip() if os.path.exists(p) else ""
```

- [ ] **Step 4: Rewrite the keep/revert body + record call in `main()`**

In `main()`, replace the block from `if baseline and not math.isnan(cand):` through the `_record_experiment(cand, best, kept)` call (current lines 47–77) with:

```python
    # capture the implement footprint, proposal, and summary BEFORE commit/revert
    # — a revert's `git clean` wipes the untracked proposals/ dir
    files_changed = _changed_files()
    proposal = _read_proposal()
    summary = _read_summary()

    if baseline and not math.isnan(cand):
        git("add", "-A")
        git("commit", "-m", f"saage: baseline score {cand}")
        commit_sha = git_out("rev-parse", "HEAD") or None
        best, fails, status, kept = cand, 0, "keep", True
    else:
        # strict inequality: ties revert (no equal-score churn); NaN candidates
        # (failed train/eval) compare False and revert in both directions
        improved = (cand < best) if lower else (cand > best)
        if improved:
            git("add", "-A")
            git("commit", "-m", f"saage: keep score {cand}")
            commit_sha = git_out("rev-parse", "HEAD") or None
            best, fails, status, kept = cand, 0, "keep", True
        else:
            # preserve the research log across the revert (excluded files —
            # data, ledger, proposals — are untouched by checkout/clean)
            saved = (open("research_log.md").read()
                     if os.path.exists("research_log.md") else "")
            git("checkout", "--", ".")
            git("clean", "-fd")
            if saved:
                open("research_log.md", "w").write(saved)
            commit_sha = None
            fails, status, kept = fails + 1, "revert", False

    target_met = 0
    if args.target not in ("", "none", "None") and not math.isnan(best):
        t = float(args.target)
        target_met = int(best <= t if lower else best >= t)

    # rich record: terse summary + outcome -> research_log (proposer's working
    # memory); full proposal -> experiments.jsonl (human record / report)
    _record_experiment(cand, best, kept, commit_sha, files_changed, proposal, summary)

    print(f"RESULT={status} BEST_SCORE={best} FAILURES={fails} TARGET_MET={target_met}")
```

(Note: this deletes the old `with open("research_log.md", "a") ... f.write(f"- candidate=...")` line — the terse entry is now written inside `_record_experiment` → `_append_research_log`.)

- [ ] **Step 5: Replace `_record_experiment` + add `_append_research_log` and `_fmt`**

Replace the whole `_record_experiment` function (current lines 82–94) with:

```python
def _fmt(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x:g}"


def _append_research_log(step: int, candidate: float, best: float, kept: bool,
                         commit_sha: str | None, files_changed: list[str],
                         summary: str) -> None:
    """Append the terse entry the next propose/critic agent reads: a one-paragraph
    change summary + the files actually changed + the outcome. The summary (not
    the full proposal) keeps this log small enough to re-read every iteration."""
    result = "KEPT ✅" if kept else "reverted ❌"
    files = ", ".join(files_changed) or "none"
    sha = (commit_sha or "")[:8]
    body = summary or "(no summary written)"
    with open("research_log.md", "a") as f:
        f.write(
            f"\n## Experiment {step} — {result} "
            f"(candidate={_fmt(candidate)}, best={_fmt(best)})\n"
            f"- changed: {files}\n"
            + (f"- commit: {sha}\n" if sha else "")
            + f"\n{body}\n"
        )


def _record_experiment(candidate: float, best: float, kept: bool,
                       commit_sha: str | None, files_changed: list[str],
                       proposal: str, summary: str) -> None:
    rows = []
    if os.path.exists("experiments.jsonl"):
        with open("experiments.jsonl") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    step = len(rows) + 1
    # parent_step = the most recent KEPT step (the experiment this branched off);
    # 0 = the baseline
    parent_step = next((r["step"] for r in reversed(rows) if r.get("kept")), 0)
    _append_research_log(step, candidate, best, kept, commit_sha,
                         files_changed, summary)
    record = {"step": step, "parent_step": parent_step,
              "candidate": None if math.isnan(candidate) else candidate,
              "best": None if math.isnan(best) else best,
              "kept": kept, "commit_sha": commit_sha,
              "files_changed": files_changed, "summary": summary,
              "proposal": proposal}
    with open("experiments.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kaggle_keep_or_revert.py -q`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add flows/kaggle_solver/keep_or_revert.py tests/test_kaggle_keep_or_revert.py
git commit -m "feat(kaggle): anchor experiment ledger + terse summary research_log"
```

---

### Task 2: `summarize` agent + flow wiring + artifacts

**Files:**
- Create: `flows/kaggle_solver/summarize/skill.md`
- Modify: `flows/kaggle_solver/flow.yaml`
- Test: `tests/test_flows_hydrate.py` (existing — no edit; it auto-discovers the flow)

**Interfaces:**
- Produces: `proposals/summary.md` (one paragraph), consumed by Task 1's `_read_summary()`.

- [ ] **Step 1: Create the summarize skill**

Create `flows/kaggle_solver/summarize/skill.md`:

```markdown
---
name: summarize
description: |
  Condense the current kaggle experiment proposal into one short paragraph for
  the running research log.
tools: [read_file, write_file]
---
SKILL_ID: summarize

You are the proposal summarizer. You have ONE job: condense the current proposal
into a single short paragraph for the running research log. Do NOT propose,
implement, critique, or run anything.

1. Read `proposals/latest.md`.
2. Write a ONE-paragraph plain-English summary to `proposals/summary.md`:
   - 2–4 sentences, under ~60 words, no code, no markdown headers/bullets.
   - State WHAT changes (the concrete feature/model/hyperparameter change and the
     before→after if given, e.g. "TF-IDF ngram_range (1,1)→(1,2)") and WHY (the
     hypothesis in a phrase).
   - This is the only record the next proposer reads about this experiment, so
     be specific and faithful to the proposal — do not editorialize or add
     ideas that are not in it.
3. Reply with the same one-paragraph summary as your final message.
```

- [ ] **Step 2: Wire the summarize step into the flow**

In `flows/kaggle_solver/flow.yaml`, find the end of `propose_loop` (the `check: { id: proposal_critic, ... }` line, ~line 111) and the start of `implement_loop` (~line 112). Insert the summarize step between them:

```yaml
        check: { id: proposal_critic, type: agent, skill: proposal_critic, max_steps: 8 }
      # one-paragraph digest of the accepted proposal -> proposals/summary.md, which
      # keep_or_revert writes into research_log.md (kept terse so the proposer can
      # re-read the whole log cheaply every iteration; the full proposal goes only
      # to experiments.jsonl for the human report)
      - { id: summarize, type: agent, skill: summarize, max_steps: 6 }
      - id: implement_loop
```

- [ ] **Step 3: Add the artifacts key**

In `flows/kaggle_solver/flow.yaml`, add an `artifacts:` key at the top level (after the `workspace: /tmp/saage_kaggle` line, before `shared:`):

```yaml
workspace: /tmp/saage_kaggle
# what the remote sidecar collects from the workspace (local runs ignore this)
# (superseded — the report agent makes this report.html, not report_narrative.md)
artifacts: [experiments.jsonl, research_log.md, report.html]
shared:
```

- [ ] **Step 4: Run the hydrate test to verify the flow + skill wire**

Run: `python -m pytest tests/test_flows_hydrate.py -q`
Expected: PASS (the kaggle_solver case hydrates with the new `summarize` skill and step).

- [ ] **Step 5: Commit**

```bash
git add flows/kaggle_solver/summarize/skill.md flows/kaggle_solver/flow.yaml
git commit -m "feat(kaggle): summarize agent + artifacts key"
```

---

### Task 3: Per-run ledger reset in `setup_competition.py`

**Files:**
- Modify: `flows/kaggle_solver/setup_competition.py:102-106`
- Test: `tests/test_kaggle_setup_reset.py` (create)

**Interfaces:**
- Consumes: nothing new. Resets `research_log.md` + `experiments.jsonl` at run start.

- [ ] **Step 1: Write the failing test**

Create `tests/test_kaggle_setup_reset.py`:

```python
"""setup_competition.py resets the ledger per run (offline).

A reused kaggle workspace must not carry a prior run's research_log/experiments
into this run (the non-monotonic-best bug). Setup runs once per fresh run.
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "flows" / "kaggle_solver" / "setup_competition.py")


def _run(repo):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--comp", "demo-comp",
         "--metric", "accuracy", "--lower-is-better", "false",
         "--short-epochs", "15", "--final-epochs", "100", "--branch", "saage-kaggle"],
        cwd=repo, capture_output=True, text=True)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "task.md").write_text("# demo competition\n")
    return tmp_path


def test_setup_resets_stale_ledger(workspace):
    # simulate a prior run's leftovers in a reused workspace
    (workspace / "research_log.md").write_text("STALE prior-run content\n")
    (workspace / "experiments.jsonl").write_text(
        '{"step": 1, "best": 99}\n{"step": 2, "best": 98}\n')
    r = _run(workspace)
    assert r.returncode == 0, r.stderr
    log = (workspace / "research_log.md").read_text()
    assert "STALE prior-run content" not in log          # reset, fresh header
    assert "kaggle solver research log" in log
    assert not (workspace / "experiments.jsonl").exists()  # wiped for the new run
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_kaggle_setup_reset.py -q`
Expected: FAIL — current setup only seeds research_log if absent (so `STALE prior-run content` survives), and never removes `experiments.jsonl`.

- [ ] **Step 3: Replace the create-if-absent seeding with an unconditional reset**

In `flows/kaggle_solver/setup_competition.py`, replace the block (current lines 102–106):

```python
    if not Path("research_log.md").exists():
        direction = "lower" if str(args.lower_is_better).lower() == "true" else "higher"
        Path("research_log.md").write_text(LOG_HEADER.format(
            comp=args.comp, metric=args.metric, direction=direction,
            short_epochs=args.short_epochs, final_epochs=args.final_epochs))
```

with:

```python
    # Reset the ledger so each run starts clean. research_log.md / experiments.jsonl
    # are git-excluded and persist on a REUSED workspace, so a prior run's rows would
    # otherwise concatenate into this run's report AND the proposer's context (the
    # non-monotonic "best"). setup is a one-shot top-level step; a resumed run
    # re-enters at a later step, so its in-progress ledger is preserved.
    direction = "lower" if str(args.lower_is_better).lower() == "true" else "higher"
    Path("research_log.md").write_text(LOG_HEADER.format(
        comp=args.comp, metric=args.metric, direction=direction,
        short_epochs=args.short_epochs, final_epochs=args.final_epochs))
    Path("experiments.jsonl").unlink(missing_ok=True)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_kaggle_setup_reset.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add flows/kaggle_solver/setup_competition.py tests/test_kaggle_setup_reset.py
git commit -m "fix(kaggle): reset ledger per run in setup_competition"
```

---

### Final verification

- [ ] **Run the full suite**

Run: `python -m pytest -q`
Expected: all green (existing 310 + the new kaggle tests).

- [ ] **Sanity-check the kaggle flow hydrates standalone**

Run: `python -c "from saage.hydrate import build_flow; build_flow('flows/kaggle_solver/flow.yaml', provider=object(), workspace='/tmp/x'); print('ok')"`
Expected: `ok`.
