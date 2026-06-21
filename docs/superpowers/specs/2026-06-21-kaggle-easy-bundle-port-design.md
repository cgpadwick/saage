# kaggle_solver Easy-Bundle Port — Design

**Status:** approved (2026-06-21)

## Goal

Port the proven hill-climb-ledger fixes already shipped on `flows/greenfield_ml`
and `contrib/lewm_hillclimb_guided` (PR #18) to `flows/kaggle_solver`, so the
kaggle research log is a faithful, terse, per-run record the proposer can reason
over — and the experiment ledger anchors to what was actually done.

Scope = the **easy bundle**: ledger anchoring (#1), terse research_log + a
`summarize` agent (#2+#3), per-run ledger reset (#4), generated-output filtering
(#7), and an `artifacts:` key. **Deferred:** verify diff==proposal (#5) and the
HTML report (#6) — both are design changes, tracked separately.

## Reference implementations

`contrib/lewm_hillclimb_guided` is the closest analogue (persistent reused
workspace, baseline recorded via `keep_or_revert.py --baseline true`, setup
resets the ledger). `flows/greenfield_ml` is the secondary reference (the
`tests/test_keep_or_revert.py` shape). Port = mirror these; do not redesign.

## Current kaggle_solver state (what exists)

Flow order (`flows/kaggle_solver/flow.yaml`): `prepare → setup → understand_loop
→ eda_loop → baseline_build → baseline_train_loop → baseline_record → hillclimb
(propose_loop → implement_loop → reset_candidate → train_loop → keep_or_revert)
→ final_train_loop → submission_loop → report_narrative → grade`.

- `keep_or_revert.py` `_record_experiment` writes only `step, candidate, best,
  kept, proposal`; research_log gets a bare `- candidate=X best=Y -> status`
  line. No `commit_sha`/`parent_step`/`files_changed`, no `_changed_files`, no
  `git_out`.
- `setup_competition.py` seeds research_log only create-if-absent; never resets
  `experiments.jsonl`. Workspace is **persistent/reused** (ledger git-excluded)
  → prior-run rows concatenate into this run (the non-monotonic-`best` bug).
- No `summarize/` skill. `propose` writes `proposals/latest.md`.
- `verify_training` checks *training integrity* post-train (deferred #5).
- No `report.py`; flow ends at `report_narrative` + `grade` (deferred #6).
- **No `artifacts:` key.**
- `research_log.md` is touched only by: `keep_or_revert.py`, `setup_competition.py`,
  and read by `propose`/`proposal_critic`/`report_narrative`. `comp_understanding`
  and `eda` do NOT write it → resetting at `setup` is safe.

## Changes

### 1. `flows/kaggle_solver/keep_or_revert.py` — ledger anchoring + terse log

Mirror greenfield/lewm:

- Add `git_out(*args)` (returns stdout) alongside the existing `git()`.
- `_LEDGER_FILES = {"research_log.md", "experiments.jsonl", "eval_results.json",
  "submission.csv", "training.log"}`.
- `_changed_files()` = `git diff --name-only HEAD` + `git ls-files --others
  --exclude-standard`, dropping `_LEDGER_FILES`, anything under `proposals/`, and
  anything under `checkpoints/`.
- Capture `files_changed`, `proposal` (`proposals/latest.md`), and `summary`
  (`proposals/summary.md`) **before** the commit/revert (a revert's `git clean`
  wipes untracked `proposals/`).
- `commit_sha = git_out("rev-parse","HEAD") or None` on a keep (and on the
  `--baseline true` keep); `None` on revert.
- `_record_experiment(...)` adds `parent_step` (most recent kept step; baseline →
  0), `commit_sha`, `files_changed`, `summary`; keeps full `proposal`. Writes
  `experiments.jsonl`.
- `_append_research_log(...)` writes the terse entry — `## Experiment N —
  KEPT/reverted (candidate=.., best=..)` + `- changed: <files>` + `- commit:
  <sha>` (kept only) + the one-paragraph `summary` — replacing the bare line.
- `_read_summary()` reads `proposals/summary.md` (fallback `"(no summary
  written)"`).

nan-safe: anchoring is score-independent; existing nan handling and the
`candidate=None`/`best=None` jsonl coercion are unchanged.

### 2. `flows/kaggle_solver/summarize/skill.md` — new agent

Single purpose: read `proposals/latest.md` → write a one-paragraph (≤~60 words,
no code/headers) `proposals/summary.md`, faithful to the proposal. kaggle-worded
(feature engineering / model / hyperparameter knobs). Reply with the same
paragraph. `tools: [read_file, write_file]`.

### 3. `flows/kaggle_solver/flow.yaml`

- Insert `- { id: summarize, type: agent, skill: summarize, max_steps: 6 }`
  between `propose_loop` and `implement_loop`.
- Add `artifacts: [experiments.jsonl, research_log.md, report_narrative.md]`
  (report.html added when #6 lands).

### 4. `flows/kaggle_solver/setup_competition.py` — per-run reset

Replace the create-if-absent research_log seeding with an unconditional reset:
write the research_log header fresh and `Path("experiments.jsonl").unlink(
missing_ok=True)`. Resume-safe by construction: `setup` is a one-shot top-level
step, so a resumed run (which re-enters at a later step) never re-runs it.

### 5. Tests — `tests/test_kaggle_keep_or_revert.py`

New file mirroring `tests/test_keep_or_revert.py` (offline; runs the real script
as a subprocess in a throwaway git repo that gitignores `experiments.jsonl`):

- keep records `commit_sha == HEAD`, `parent_step`, and `files_changed`.
- revert records `commit_sha is None` but still records `files_changed`.
- `files_changed` excludes bookkeeping AND generated outputs (`eval_results.json`,
  `submission.csv`, `training.log`).
- research_log carries the one-paragraph summary (from `proposals/summary.md`),
  not the full proposal; full proposal lands in `experiments.jsonl`.
- summary captured before a revert wipes `proposals/`.
- baseline (`--baseline true`) records `parent_step == 0` and a `commit_sha`.

`tests/test_flows_hydrate.py` already guards that the flow + new `summarize`
skill hydrate.

## Out of scope (deferred, separate design)

- #5 verify diff==proposal (kaggle's pre-train gate is a pytest command, not an
  agent — architectural).
- #6 HTML `report.py` (needs adapting for nan-range metrics + leaderboard
  medal/grade).

## Testing strategy

Offline unit tests (above) + the existing hydrate guard. No live provider or
kaggle data needed. Full suite must stay green (`python -m pytest -q`).
