# LeWM Task-Specialization Ceiling — Experiment Design

Date: 2026-06-27
Status: approved (brainstorming) → ready for implementation plan
Flow: `contrib/lewm_hillclimb_guided/` (extends the existing guided hill-climb)
Companion repo: `le-wm` (branch `feat/eval-holdout-split`)

## 1. Research question

How much success-rate headroom exists on **OGBench-Cube** if we *specialize*
LeWM's training recipe on that one task, versus the paper's deliberately
**transferable** fixed recipe — and how far does specialization close the gap to
DINO-WM (86%)?

The paper (*LeWorldModel*, Maes et al.) keeps "hyperparameters fixed across all
environments" and treats λ (SIGReg weight) as essentially the only knob — a
*transferability* stance. This experiment asks the opposite question: drop the
transferability constraint, per-task-tune the recipe on cube under a fixed
training budget, and measure the ceiling.

## 2. Claim structure (layered)

- **Reference low** — paper-recipe LeWM on cube, retrained and scored by us
  (paper reports 74; our earlier measured baseline ~76; a prior spark run logged
  64.0 for bs-256 @ 8 epochs, i.e. the number is budget/seed sensitive — hence we
  re-measure it inside this flow rather than quoting a constant).
- **Output** — the best specialized recipe found by the hill-climb.
- **Headline** — `specialization_gain = specialized_test − paper_recipe_test`
  (percentage points), both at the same budget and the same held-out test set,
  positioned on a line against DINO-WM = 86%.

- **Independent variable:** the training recipe (see §5 search space).
- **Held constant:** epochs (paper's 10 for the headline; 8 as the cheap in-loop
  proxy), the eval protocol, the model identity (JEPA, ViT-Tiny encoder), the
  dataset.
- **Controlled for:** test-set selection bias (held-out test, scored once) and —
  within budget — eval noise (heavy test set for the headline).

- **Scope:** cube only (n=1 task — stated as a caveat, not hidden).

## 3. Flow architecture (approach B — symmetric headline)

Extends `contrib/lewm_hillclimb_guided/flow.yaml`. New/changed steps marked.

```
setup
│
├─ paper_headline_clean / _train / _eval            ← NEW (runs FIRST; working tree
│     train paper recipe @ confirm_epochs (10)          is pristine = paper recipe,
│     eval split=test, num_eval={{test_num_eval}}     → so no git checkout needed)
│                                                     → PAPER_TEST_SCORE (reference low)
│
├─ baseline_clean / _train / _eval / _record         ← existing; LOOP-SEED ONLY
│     train @ train_epochs (8), eval split=val (50)  → best_score (seeds keep_or_revert)
│
├─ hillclimb counting_loop (max_iterations)          ← existing
│     [propose → proposal_critic] ×3
│     summarize
│     [implement → verify] ×3
│     reset_candidate → clean → train @8
│     → eval split=val (50) → keep_or_revert
│
├─ confirm_clean / _train / _eval                    ← existing; = SPECIALIZED headline
│     train winner @ confirm_epochs (10)
│     eval split=test, num_eval={{test_num_eval}}    → SPECIALIZED_TEST_SCORE
│
├─ gain_record                                       ← NEW
│     gain = SPECIALIZED_TEST_SCORE − PAPER_TEST_SCORE  (appended to research_log.md)
│
└─ report / report_commit                            ← existing (skill updated, §6)
```

**New shared vars:** `test_num_eval: 200` (heavy held-out test size, tunable),
`paper_test_score: -1.0`.

**Why paper-headline runs first:** at flow start the working tree *is* the paper
recipe, so the reference number is obtained with zero git gymnastics. Both
headline evals use `split=test` + the same `num_eval` + the same seed → they draw
**the same test episodes** → a clean paired comparison whose difference is the
headline.

**Cost:** adds one `confirm_epochs` paper-recipe train (~25h at full scale on the
target box). The pilot shrinks all epochs + `test_num_eval` so the whole graph
runs in a couple hours to validate wiring.

## 4. Eval protocol

All three evals run `eval.py` on cube, `solver.n_steps=10` (frozen CEM budget).

| Eval              | When                       | split  | num_eval          | seed | Purpose                              |
|-------------------|----------------------------|--------|-------------------|------|--------------------------------------|
| val               | baseline + every loop iter | `val`  | 50                | 42   | hill-climb selection (keep_or_revert)|
| paper test        | once, at start             | `test` | `test_num_eval`   | 42   | reference-low headline               |
| specialized test  | once, at end               | `test` | `test_num_eval`   | 42   | specialized headline                 |

- **val pool ∩ test pool = ∅** — guaranteed by the fixed master-seed partition in
  le-wm `eval.py` (the `feat/eval-holdout-split` change): `valid_indices` is split
  into disjoint halves by `np.random.default_rng(0)`, val draws from one half,
  test from the other. val never leaks into either headline.
- Both headline evals draw the **same** test episodes (same split + num_eval +
  seed) → paired, directly subtractable.
- `test_num_eval` is guarded in `eval.py`: it must be ≤ the test-pool half size,
  else a clear `ValueError`. Pilot uses a small value; full run 200 (bump to 500
  if the test pool and wall-clock allow).
- The selection metric (val, 50 episodes) is intentionally cheap and therefore
  **noisy** (binomial SE ≈ ±7% near 50%). This is acceptable because only the
  held-out test headline is the claim; the hill-climb trajectory is exploratory.

## 5. Search space (defines the "specialization ceiling")

**Frozen — agents may not touch (enforced by propose / proposal_critic / verify
skills; `config/eval/`, `eval.py`, the eval seed, `solver.n_steps`,
`trainer.max_epochs` are already forbidden):**
- the eval protocol, CEM budget, epochs, the JEPA / ViT-Tiny model identity, the
  dataset. **Additionally forbidden (protocol, not recipe): `split`,
  `eval.num_eval`, `test_num_eval`** — an agent must not be able to widen its own
  test set or read the test split.

**Tunable by the proposer (the recipe):**
- `lr`, `λ` (sigreg weight), `history_size`, `num_preds`, `embed_dim`, predictor
  size / heads / dropout, data augmentation, batch size, warmup / schedule.

Ceiling interpretation: "best recipe at the same training budget" — recipe quality
is isolated from compute (epochs fixed).

## 6. Writeup / `report.html`

Extend the existing `report` agent skill to tell the layered story:

1. **Headline up front:** "Specializing LeWM on cube: paper-recipe X% →
   specialized Y% (+Z pts), held-out test (`test_num_eval` eps, single seed).
   DINO-WM = 86%."
2. **What changed:** winning recipe vs paper recipe — a diff table (lr, λ, history,
   …) read from the config YAMLs.
3. **Hill-climb trajectory:** existing best-so-far SVG chart (keeps = green dots,
   reverts = red ✕), relabeled **validation**, with a note that the headline is the
   separate held-out test.
4. **Honesty box:** n=1 task; single-seed headline; val selection metric is noisy
   (50 eps); only the held-out test number is the claim; multi-seed CIs are future
   work.
5. **Method:** the val/test split, frozen protocol, fixed budget.

The blog post draws from `report.html` + `research_log.md` + `experiments.jsonl`.

## 7. Execution — pilot then full, on `spark-c0c0`

`spark-c0c0` is a GB10 (DGX Spark), reachable passwordless from the dev laptop.

**Present on the box:** GPU, `~/code/le-wm`, `.venv` (Py 3.10.19), 211G datasets
under `~/.stable-wm`, `~/code/saage`.

**Hard dependency — currently UNMET, must sync before any run:**
- le-wm on spark has **no** split change (`eval.py` lacks `split`, `cube.yaml`
  lacks `split:`), and is on a prior run branch (`saage-spark-run4`). The
  `feat/eval-holdout-split` change must land on spark's le-wm base before the run.
- saage on spark is `master`; it must carry the updated flow
  (`fix/lewm-hillclimb-holdout-test`).
- If `split=val/test` runs against a le-wm without the change, Hydra errors — the
  whole design rests on this sync.

**Pilot (validate wiring, ~couple hours, numbers meaningless):**
- `--set train_epochs=1 confirm_epochs=2 test_num_eval=20 max_iterations=2`
  (trim propose/implement retry loops if needed).
- Launch via `saage remote handoff contrib/lewm_hillclimb_guided/flow.yaml
  --target spark-c0c0 --need-gpu` (register the target first with
  `saage remote add-target spark-c0c0 …` if not already in `credentials.toml`).
- **Acceptance checks:**
  1. `eval.py` prints disjoint val vs test episode indices.
  2. all three scores captured (`paper_test_score`, per-iter val, `specialized_test_score`).
  3. `gain_record` computes the subtraction.
  4. `report.html` renders with the layered headline + DINO-WM line.

**Full run (after pilot passes):**
- `--set train_epochs=8 confirm_epochs=10 test_num_eval=200 max_iterations=8`
  (bump iterations / `test_num_eval` if the box + wall-clock allow).
- Monitor via `saage remote status` / `logs`; fetch with `saage remote fetch`.

## 8. Out of scope / future work

- Multi-seed headline error bars (paper-style ±variance) — passed on for budget;
  the single biggest rigor upgrade if a reviewer pushes back.
- Additional tasks (PushT, Reacher) to test whether the specialization gain
  generalizes beyond cube.
- Tuning the training budget itself (epochs) — deliberately frozen here to keep
  recipe quality separate from compute.

## 9. Dependencies summary

- saage branch `fix/lewm-hillclimb-holdout-test` (flow + report skill).
- le-wm branch `feat/eval-holdout-split` (split logic in `eval.py` + `cube.yaml`).
- Both must be live wherever the run executes (spark-c0c0).
