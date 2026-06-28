# LeWM Task-Specialization Ceiling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `lewm_hillclimb_guided` flow so it measures the *task-specialization ceiling* on OGBench-Cube: a paper-recipe headline and a specialized-winner headline, both retrained at the same budget and scored once on a disjoint held-out test, with the gap as the output.

**Architecture:** The existing guided hill-climb already selects on a `split=val` metric and confirms the winner on `split=test` (this branch + le-wm `feat/eval-holdout-split`). This plan adds the *symmetric reference* (approach B): a paper-recipe headline trained first (pristine tree) on the same held-out test, a heavy test size (`test_num_eval`), and a deterministic `gain_record` helper. Determinism stays in code/YAML; only recipe content comes from the LLM.

**Tech Stack:** Python 3.10, PocketFlow-based saage engine, Hydra-configured le-wm training/eval, pytest (offline), `saage remote` ssh handoff to `spark-c0c0` (GB10).

## Global Constraints

- Determinism is the product: control flow stays in YAML/helpers; the LLM proposes recipe content only. (CLAUDE.md)
- All run state lives in the shared store and must be JSON-serializable. (CLAUDE.md)
- Tests are offline, no API key, reproducible: `python -m pytest` (not bare `pytest`). (memory: pytest-invocation)
- Helper scripts are deterministic (no LLM), run with cwd = le-wm workspace, and print `KEY=value` lines for the flow's `set:` captures. (existing `keep_or_revert.py` / `clean_ckpt.py` pattern)
- Frozen protocol — never tune in the recipe: `eval.py`, anything under `config/eval/` (incl. `split`, `seed`, `num_eval`, CEM `solver.n_steps`), `trainer.max_epochs`. Headline test size `test_num_eval` is harness-controlled (flow var), never agent-editable.
- Eval contract: `eval.py` prints `'success_rate': <float>`; the flow captures it via the regex `"'success_rate': (-?[0-9.]+)"`.
- Checkpoint dirs are deletable only if in `clean_ckpt.py:ALLOWED`; user checkpoints are protected.
- Both code branches must be live wherever the run executes: saage `fix/lewm-hillclimb-holdout-test`, le-wm `feat/eval-holdout-split`.

---

### Task 1: Fix and extend `clean_ckpt.py` ALLOWED set

The new paper-recipe headline trains into its own checkpoint dir (`lewm_cube_paper`); it must be cleanable. While here, fix the pre-existing bug: `confirm_clean` cleans `lewm_cube_confirm`, which is not in `ALLOWED`, so that step currently exits 1.

**Files:**
- Modify: `contrib/lewm_hillclimb_guided/clean_ckpt.py` (the `ALLOWED` set)
- Test: `tests/test_clean_ckpt_guided.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `clean_ckpt.ALLOWED` now includes `"lewm_cube_confirm"` and `"lewm_cube_paper"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_clean_ckpt_guided.py`:

```python
"""ALLOWED set of the guided-flow clean_ckpt helper."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "contrib/lewm_hillclimb_guided/clean_ckpt.py"


def _load():
    spec = importlib.util.spec_from_file_location("guided_clean_ckpt", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_allowed_covers_every_name_the_flow_cleans():
    allowed = _load().ALLOWED
    # every checkpoint dir the flow asks clean_ckpt to remove must be allowed
    for name in ("lewm_cube_exp", "lewm_smoke", "lewm_cube_confirm", "lewm_cube_paper"):
        assert name in allowed, f"{name} must be cleanable by the flow"


def test_user_checkpoints_still_protected():
    allowed = _load().ALLOWED
    for name in ("lewm", "lewm_cube", "lewm_reacher"):
        assert name not in allowed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clean_ckpt_guided.py -q`
Expected: FAIL — `lewm_cube_confirm` / `lewm_cube_paper` not in `ALLOWED`.

- [ ] **Step 3: Update ALLOWED**

In `contrib/lewm_hillclimb_guided/clean_ckpt.py`, replace the `ALLOWED` line:

```python
# explicit allow-list: the only directories this script may ever delete
ALLOWED = {"lewm_cube_exp", "lewm_cube_best", "lewm_smoke",
           "lewm_cube_confirm",   # the winner-confirmation retrain
           "lewm_cube_paper"}     # the paper-recipe headline retrain
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_clean_ckpt_guided.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_clean_ckpt_guided.py contrib/lewm_hillclimb_guided/clean_ckpt.py
git commit -m "fix(lewm_hillclimb_guided): allow confirm + paper checkpoint dirs in clean_ckpt"
```

---

### Task 2: `gain_record.py` helper (deterministic gain computation)

Replaces an inline echo with a tested helper that computes the specialization gain and appends a human-readable line to `research_log.md`.

**Files:**
- Create: `contrib/lewm_hillclimb_guided/gain_record.py`
- Test: `tests/test_gain_record.py` (create)

**Interfaces:**
- Consumes: CLI args `--specialized <float> --paper <float> --dino 86.0`.
- Produces: prints `GAIN=<specialized-paper>` (one decimal); appends a `## Specialization result` block to `research_log.md` in the cwd. Exit 0 always (never aborts the run).

- [ ] **Step 1: Write the failing test**

Create `tests/test_gain_record.py`:

```python
"""gain_record helper: computes specialized-minus-paper gain, logs it."""
import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "contrib/lewm_hillclimb_guided/gain_record.py"


def _run(tmp_path, *args):
    return subprocess.run([sys.executable, str(_PATH), *args],
                          cwd=tmp_path, capture_output=True, text=True)


def test_prints_gain_and_writes_log(tmp_path):
    r = _run(tmp_path, "--specialized", "82.0", "--paper", "76.0", "--dino", "86.0")
    assert r.returncode == 0
    assert "GAIN=6.0" in r.stdout
    log = (tmp_path / "research_log.md").read_text()
    assert "82" in log and "76" in log and "86" in log
    assert "6.0" in log


def test_negative_gain_is_reported_not_clamped(tmp_path):
    r = _run(tmp_path, "--specialized", "74.0", "--paper", "76.0", "--dino", "86.0")
    assert "GAIN=-2.0" in r.stdout


def test_failure_sentinel_paper_does_not_crash(tmp_path):
    # a crashed paper headline reads -1; the helper must still exit 0
    r = _run(tmp_path, "--specialized", "80.0", "--paper", "-1", "--dino", "86.0")
    assert r.returncode == 0
    assert "GAIN=" in r.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gain_record.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write the helper**

Create `contrib/lewm_hillclimb_guided/gain_record.py`:

```python
#!/usr/bin/env python3
"""Record the task-specialization gain (deterministic — no LLM).

gain = specialized_test_score - paper_recipe_test_score, both measured on the
same held-out test split. Appends a human-readable block to research_log.md and
prints `GAIN=<value>` for the flow's `set:` capture. Never aborts the run: a
crashed headline reads as the -1 sentinel and is reported as-is.
"""
from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--specialized", type=float, required=True)
    ap.add_argument("--paper", type=float, required=True)
    ap.add_argument("--dino", type=float, default=86.0)
    args = ap.parse_args()

    gain = round(args.specialized - args.paper, 1)
    with open("research_log.md", "a") as f:
        f.write(
            "\n## Specialization result (held-out test, single seed)\n"
            f"- paper-recipe test success_rate: {args.paper:g}\n"
            f"- specialized test success_rate: {args.specialized:g}\n"
            f"- specialization gain: {gain:+g} points\n"
            f"- reference: DINO-WM = {args.dino:g}\n"
        )
    print(f"GAIN={gain}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gain_record.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_gain_record.py contrib/lewm_hillclimb_guided/gain_record.py
git commit -m "feat(lewm_hillclimb_guided): gain_record helper for specialization gain"
```

---

### Task 3: Wire approach-B steps into `flow.yaml`

Add the heavy-test var, the paper-recipe headline (runs first, pristine tree), the heavy `num_eval` on both headline evals, and the `gain_record` step.

**Files:**
- Modify: `contrib/lewm_hillclimb_guided/flow.yaml`
- Test: `tests/test_lewm_specialization_flow.py` (create)

**Interfaces:**
- Consumes: `gain_record.py` (Task 2), `clean_ckpt.py` ALLOWED with `lewm_cube_paper` (Task 1).
- Produces: shared keys `test_num_eval`, `paper_test_score`, `paper_name`; steps `paper_headline_clean`, `paper_headline_train`, `paper_headline_eval`, `gain_record`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lewm_specialization_flow.py`:

```python
"""Structural checks on the specialization flow.yaml."""
from pathlib import Path
import yaml

_FLOW = Path(__file__).resolve().parent.parent / "contrib/lewm_hillclimb_guided/flow.yaml"


def _load():
    return yaml.safe_load(_FLOW.read_text())


def test_new_shared_vars_present():
    shared = _load()["shared"]
    assert shared["test_num_eval"] == 200
    assert shared["paper_test_score"] == -1.0
    assert shared["paper_name"] == "lewm_cube_paper"


def test_paper_headline_and_gain_steps_present_and_ordered():
    ids = [s["id"] for s in _load()["workflow"]]
    for sid in ("paper_headline_clean", "paper_headline_train",
                "paper_headline_eval", "gain_record"):
        assert sid in ids, f"{sid} missing"
    # paper headline runs before the hill-climb (pristine tree); gain after confirm
    assert ids.index("paper_headline_eval") < ids.index("hillclimb")
    assert ids.index("gain_record") > ids.index("confirm_eval")


def test_headline_evals_use_test_split_and_heavy_num_eval():
    steps = {s["id"]: s for s in _load()["workflow"]}
    for sid in ("paper_headline_eval", "confirm_eval"):
        run = steps[sid]["run"]
        assert "split=test" in run, f"{sid} must eval split=test"
        assert "eval.num_eval={{ test_num_eval }}" in run, f"{sid} must use test_num_eval"


def test_loop_selection_stays_on_val():
    # the in-loop eval and baseline_eval keep split=val (cheap selection)
    flat = _FLOW.read_text()
    assert "split=val" in flat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lewm_specialization_flow.py -q`
Expected: FAIL — new vars/steps absent; `confirm_eval` lacks `eval.num_eval`.

- [ ] **Step 3a: Add shared vars**

In `flow.yaml`, in the `shared:` block, after the `confirm_score: -1.0` line add:

```yaml
  paper_test_score: -1.0         # paper-recipe headline on the held-out TEST split
  test_num_eval: 200             # held-out TEST size for the headline evals (heavy);
                                 # in-loop val stays at cube.yaml's 50 (cheap selection)
  paper_name: lewm_cube_paper    # checkpoint dir for the paper-recipe headline retrain
```

- [ ] **Step 3b: Add the paper-recipe headline steps (run FIRST, after `setup`)**

In `flow.yaml`, immediately after the `setup` step and before `baseline_clean`, insert:

```yaml
  # ---- paper-recipe headline: the REFERENCE-LOW number. Runs first, while the
  # working tree is still the pristine paper recipe (no git checkout needed).
  # Same budget + same held-out TEST split as the specialized headline, so the
  # two are directly subtractable (gain_record).
  - id: paper_headline_clean
    type: command
    run: 'STABLEWM_HOME="{{ stablewm_home }}" {{ python }} "{{ flow_dir }}/clean_ckpt.py" --name {{ paper_name }}'
  - id: paper_headline_train
    type: command
    run: 'STABLEWM_HOME="{{ stablewm_home }}" python train.py data=ogb output_model_name={{ paper_name }} subdir={{ paper_name }} trainer.max_epochs={{ confirm_epochs }} num_workers={{ num_workers }} wandb.enabled=False'
  - id: paper_headline_eval
    type: command
    run: 'STABLEWM_HOME="{{ stablewm_home }}" python eval.py --config-name=cube.yaml policy={{ paper_name }}/weights_epoch_{{ confirm_epochs }}.pt solver.n_steps=10 split=test eval.num_eval={{ test_num_eval }}'
    set: { paper_test_score: "'success_rate': (-?[0-9.]+)" }
```

- [ ] **Step 3c: Add heavy `num_eval` to the confirm headline**

In `flow.yaml`, change the `confirm_eval` `run:` line — append `eval.num_eval={{ test_num_eval }}`:

```yaml
    run: 'STABLEWM_HOME="{{ stablewm_home }}" python eval.py --config-name=cube.yaml policy={{ confirm_name }}/weights_epoch_{{ confirm_epochs }}.pt solver.n_steps=10 split=test eval.num_eval={{ test_num_eval }}'
```

- [ ] **Step 3d: Add the `gain_record` step (after `confirm_record`, before `report`)**

```yaml
  - id: gain_record
    type: command
    run: '{{ python }} "{{ flow_dir }}/gain_record.py" --specialized {{ confirm_score }} --paper {{ paper_test_score }} --dino 86.0'
    set: { specialization_gain: "GAIN=(-?[0-9.]+)" }
```

And add `specialization_gain: -999.0` to the `shared:` block (seed so the key always exists).

- [ ] **Step 3e: Update the header comment flow-map**

In the top-of-file comment, update the pipeline sketch to include `paper_headline` (after setup) and `gain_record` (after confirm), and add a one-line note: "paper_headline + confirm are the two headline numbers; gain = confirm − paper on the held-out TEST split."

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_lewm_specialization_flow.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the existing flow tests (no regressions)**

Run: `python -m pytest tests/test_setup_experiment.py tests/test_clean_ckpt_guided.py tests/test_gain_record.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add contrib/lewm_hillclimb_guided/flow.yaml tests/test_lewm_specialization_flow.py
git commit -m "feat(lewm_hillclimb_guided): symmetric paper-recipe headline + gain (approach B)"
```

---

### Task 4: Update the `report` skill for the layered headline

Teach the report agent to lead with the gain, show a recipe diff, and include the honesty box.

**Files:**
- Modify: `contrib/lewm_hillclimb_guided/report/skill.md`

**Interfaces:**
- Consumes: shared vars `paper_test_score`, `confirm_score`, `specialization_gain`, `best_score`, `target_success` (templated into the skill).
- Produces: no code interface — prose instructions to the agent.

- [ ] **Step 1: Add the inputs**

In `report/skill.md`, in the description front-matter, ensure these are present (add the gain line):

```
  Specialization gain (held-out test): {{ specialization_gain }} points
  (paper-recipe {{ paper_test_score }} -> specialized {{ confirm_score }}; DINO-WM = 86).
```

- [ ] **Step 2: Add the report sections**

In the "must contain, IN THIS ORDER" list, prepend a new section 1 (renumber the rest):

```
1. **Headline (one line, up front).** "Specializing LeWM on OGBench-Cube:
   paper-recipe {{ paper_test_score }}% -> specialized {{ confirm_score }}%
   ({{ specialization_gain }} pts), held-out test ({{ test_num_eval }} episodes,
   single seed). DINO-WM = 86%." Then a 1-2 sentence interpretation of the gap.

2. **Recipe diff.** A small table: paper recipe vs winning recipe (lr, lambda,
   history_size, num_preds, embed_dim, predictor size, augmentations) — read the
   REAL values from config/train/lewm.yaml and config/train/model/lewm.yaml.

3. **Honesty box (a callout).** n=1 task (cube only); single-seed headline
   (multi-seed CIs = future work); the in-loop selection metric is VALIDATION on
   50 episodes and is NOISY (+/-~7%), so the hill-climb trajectory is exploratory
   — only the held-out TEST headline is the claim.
```

Keep the existing experiment table + SVG chart (now sections 4-5), and the method/style sections.

- [ ] **Step 3: Verify the skill mentions the new fields**

Run: `grep -nE "specialization_gain|paper_test_score|Honesty|held-out" contrib/lewm_hillclimb_guided/report/skill.md`
Expected: matches for the gain var, the honesty box, and held-out test. (No pytest — this is an LLM prompt, validated for real in the pilot, Task 7.)

- [ ] **Step 4: Commit**

```bash
git add contrib/lewm_hillclimb_guided/report/skill.md
git commit -m "docs(lewm_hillclimb_guided): report skill tells the layered specialization story"
```

---

### Task 5: Clarify the harness-controlled test size in the proposer skills

`split`/`num_eval` are already forbidden (covered by "anything under config/eval/" + "eval seed/num_eval"). Add one explicit line so an agent never reasons about the held-out test size.

**Files:**
- Modify: `contrib/lewm_hillclimb_guided/propose/skill.md`, `.../proposal_critic/skill.md`

- [ ] **Step 1: Edit propose skill**

In `propose/skill.md`, in the STRICT CONSTRAINTS bullet that forbids touching the eval protocol, append:

```
     The held-out TEST split and its size are harness-controlled (the flow sets
     them); never reference, widen, or tune the eval set.
```

- [ ] **Step 2: Edit proposal_critic skill**

In `proposal_critic/skill.md`, in the FORBIDDEN check, add `the held-out test split/size` to the list of things that fail a proposal.

- [ ] **Step 3: Verify**

Run: `grep -niE "held-out|harness-controlled" contrib/lewm_hillclimb_guided/propose/skill.md contrib/lewm_hillclimb_guided/proposal_critic/skill.md`
Expected: a match in each file.

- [ ] **Step 4: Commit**

```bash
git add contrib/lewm_hillclimb_guided/propose/skill.md contrib/lewm_hillclimb_guided/proposal_critic/skill.md
git commit -m "docs(lewm_hillclimb_guided): forbid tuning the held-out test set"
```

---

### Task 6: Standalone held-out-split verification (le-wm has no pytest)

The disjoint-partition logic in `eval.py` (already committed on `feat/eval-holdout-split`) needs a runnable check, since le-wm has no test suite.

**Files:**
- Create: `/home/cpadwick/code/le-wm/verify_holdout_split.py` (on the le-wm `feat/eval-holdout-split` branch)

**Interfaces:**
- Consumes: nothing (synthetic pool).
- Produces: a script that exits 0 with `SPLIT OK` when val/test are disjoint + deterministic, non-zero otherwise.

- [ ] **Step 1: Write the script**

Create `/home/cpadwick/code/le-wm/verify_holdout_split.py`:

```python
#!/usr/bin/env python3
"""Standalone check of the held-out val/test partition used by eval.py.

Mirrors the sampling logic: a fixed master seed (default_rng(0)) cuts valid
start points into disjoint halves; cfg.seed then samples num_eval within a half.
Asserts val/test never overlap and val is reproducible. No deps beyond numpy.
"""
import numpy as np


def pick(valid_indices, num_eval, seed, split):
    g = np.random.default_rng(seed)
    if split == "all":
        chosen = g.choice(len(valid_indices) - 1, size=num_eval, replace=False)
        return set(np.sort(valid_indices[chosen]).tolist())
    part = np.random.default_rng(0).permutation(len(valid_indices))
    half = len(valid_indices) // 2
    pool = part[:half] if split == "val" else part[half:]
    assert len(pool) >= num_eval, f"{split} pool too small"
    chosen = g.choice(len(pool), size=num_eval, replace=False)
    return set(np.sort(valid_indices[pool[chosen]]).tolist())


def main() -> None:
    valid = np.arange(3000) * 2 + 5
    v = pick(valid, 50, 42, "val")
    t = pick(valid, 200, 42, "test")
    assert v & t == set(), f"val/test overlap: {len(v & t)} episodes"
    assert v == pick(valid, 50, 42, "val"), "val not reproducible"
    print(f"SPLIT OK  val={len(v)} test={len(t)} overlap=0")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `cd /home/cpadwick/code/le-wm && python verify_holdout_split.py`
Expected: `SPLIT OK  val=50 test=200 overlap=0`

- [ ] **Step 3: Commit (on the le-wm branch)**

```bash
cd /home/cpadwick/code/le-wm
git add verify_holdout_split.py
git commit -m "test(eval): standalone check that val/test holdout split is disjoint"
```

---

### Task 7: Sync to `spark-c0c0` and register the target

Both branches must be live on the box; datasets/venv/GPU already are.

**Files:** none (ops task).

- [ ] **Step 1: Register the target (if absent)**

Run (locally):
```bash
saage remote add-target spark-c0c0 --host spark-c0c0 --user cpadwick
saage remote status spark-c0c0 || true
```
Expected: target added / reachable. (`ssh spark-c0c0` already works passwordless.)

- [ ] **Step 2: Land the le-wm split change on spark's le-wm base**

The spark le-wm is on a prior run branch (`saage-spark-run4`) without the split logic. Push the change and merge it into the base the flow will branch from:
```bash
cd /home/cpadwick/code/le-wm
git push spark-c0c0:/home/cpadwick/code/le-wm feat/eval-holdout-split   # or via remote
```
Then on the box, ensure `eval.py` has the split + `cube.yaml` has `split: all`:
```bash
ssh spark-c0c0 'cd ~/code/le-wm && git checkout feat/eval-holdout-split && grep -c "split ==" eval.py && grep -n "^split" config/eval/cube.yaml'
```
Expected: `eval.py` split count >= 1; `cube.yaml` shows `split: all`. (If pushing to a non-bare checkout is awkward, fetch from the laptop remote or `scp` the two files — the goal is simply: spark's le-wm base has the split change.)

- [ ] **Step 3: Land the saage flow on spark**

```bash
ssh spark-c0c0 'cd ~/code/saage && git fetch && git checkout fix/lewm-hillclimb-holdout-test && git log --oneline -1'
```
Expected: HEAD is the specialization flow commit (Task 3).

- [ ] **Step 4: Confirm the run script the engine will launch is the updated venv install**

```bash
ssh spark-c0c0 'cd ~/code/le-wm && .venv/bin/python -c "import saage, sys; print(saage.__file__)"'
```
Expected: resolves to `~/code/saage/saage/...` (editable install) — so the box runs the updated engine. If it points elsewhere, re-run `uv pip install -e .` for saage in the le-wm venv.

- [ ] **Step 5: No commit** (ops task; nothing to commit locally).

---

### Task 8: Pilot run, then full run

Validate wiring end-to-end on spark with shrunk params; numbers are meaningless at 1 epoch.

**Files:** none (run task).

- [ ] **Step 1: Launch the pilot**

```bash
saage remote handoff contrib/lewm_hillclimb_guided/flow.yaml --target spark-c0c0 --need-gpu \
  --set train_epochs=1 --set confirm_epochs=2 --set test_num_eval=20 --set max_iterations=2
```

- [ ] **Step 2: Monitor**

```bash
saage remote status spark-c0c0
saage remote logs spark-c0c0
```

- [ ] **Step 3: Acceptance checks (all four must hold)**

1. `eval.py` log prints disjoint val vs test episode index arrays (val on the `split=val` evals, test on the two headline evals).
2. All three scores captured: `paper_test_score` (paper headline), per-iteration val candidate(s), `confirm_score` (specialized headline) — visible in the log / `research_log.md`.
3. `gain_record` printed `GAIN=<paper minus specialized>` and appended the "Specialization result" block to `research_log.md`.
4. `report.html` rendered with the layered headline line + DINO-WM 86 + honesty box.

- [ ] **Step 4: Fetch and eyeball artifacts**

```bash
saage remote fetch spark-c0c0
```
Open `report.html`, `research_log.md`, `experiments.jsonl` from the fetched run dir.

- [ ] **Step 5: Full run (only after the pilot passes)**

```bash
saage remote handoff contrib/lewm_hillclimb_guided/flow.yaml --target spark-c0c0 --need-gpu \
  --set train_epochs=8 --set confirm_epochs=10 --set test_num_eval=200 --set max_iterations=8
```
Bump `max_iterations` / `test_num_eval` only if the box + wall-clock allow (test pool must be >= `test_num_eval`).

- [ ] **Step 6: No code commit** (the run commits its own report via `report_commit`).

---

## Self-Review

**Spec coverage:**
- §2 layered claim (paper vs specialized vs DINO 86) → Tasks 2, 3, 4 (gain helper, flow wiring, report).
- §3 flow architecture (paper headline first, gain_record) → Task 3; checkpoint dir → Task 1.
- §4 eval protocol (val 50 / test heavy, disjoint) → Task 3 (split + num_eval), Task 6 (disjoint check).
- §5 search space frozen/tunable → Task 5 (held-out test forbidden); recipe knobs unchanged (already in propose skill).
- §6 report → Task 4.
- §7 execution (sync + pilot + full) → Tasks 7, 8.
- §8 future work (multi-seed, more tasks) → out of scope, honesty box notes it (Task 4).
- §9 dependencies → Global Constraints + Task 7.

**Placeholder scan:** No TBD/TODO; all steps show concrete code/commands. The report skill (Task 4) is prose-by-nature (LLM prompt) — validated by grep + the pilot, explicitly noted.

**Type/name consistency:** `lewm_cube_paper` (paper_name) consistent across Tasks 1/3; `paper_test_score`, `test_num_eval`, `specialization_gain`, `confirm_score` consistent across Tasks 2/3/4; `gain_record.py` flags `--specialized/--paper/--dino` match between Task 2 (helper) and Task 3 (flow invocation); `GAIN=` print matches the flow's `set:` regex.
