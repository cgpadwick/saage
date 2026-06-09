# Greenfield ML Auto-Research Flow — Design Sketch

Replicate **just the workflow** of [MLE-Beast](https://github.com/cgpadwick/mle-beast)
(no DB, no UI, no event bus) as a `saage` flow. The user gives a workspace dir, a task
prompt, and a target metric; the flow builds a baseline end-to-end ML pipeline, then
hill-climbs (Karpathy-style ML auto-research) until the target is met or the budget runs
out.

```
saage run flows/greenfield_ml/flow.yaml \
    --workspace /tmp/workspace \
    --set task="Build an image classifier for MNIST" \
    --set target_accuracy=0.98
```

---

## 1. What MLE-Beast actually does (the shape we're copying)

MLE-Beast is itself a PocketFlow graph. Its greenfield pipeline (from
`flows/full_pipeline.py`), stripped of the DB/dashboard nodes:

```
GitSetup → DataAnalysis(EDA) → [Baseline: implement → test → train → evaluate → record]
  → BaselineEval (score the baseline; early-exit if target already met)
  → [ Propose → Implement → Test → Train → Evaluate → HillClimbEval(keep/revert) ] × N
  → done
```

Key mechanics we keep:
- **Baseline first**: a *simple* working pipeline (data → train → eval), not SOTA.
- **Hill-climb loop**: each iteration is ONE experiment — propose a single change,
  implement it, verify, train, evaluate, then **keep (git commit) if the score improved
  or revert (`git checkout` + `git clean`) if it didn't**.
- **Convergence**: stop when `target met` OR `max_steps` OR `max_consecutive_failures`.
- **Score = source of truth file**: `evaluate.py` writes `eval_results.json`
  (`{"metric_name","value","split","n_samples"}`) and prints `Test accuracy: 0.78`; a
  "finder" pulls the number out (no hardcoded formats).
- **research_log.md**: append-only experiment history the proposer reads to avoid
  repeating failed ideas and to escalate when plateaued.

Mechanics we **drop** (DB/UI concerns): experiment persistence, event emission,
dashboard tags, SSE. Pure workflow only.

---

## 2. The `saage` greenfield flow

```
setup ─ download_data ─ ┌─ baseline_build (implement→verify, retry×3) ─ train ─ evaluate ─┐
                        └──────────────────────────────────────────────────────────────────┘
                                                                                           │
        ┌──────────────────────────────────────────────────────────────────────────────────┘
        ▼  counting_loop  (exit_when: best_score ≥ target  OR  consecutive_failures ≥ max)
   ┌── propose ─ build (implement→verify, retry×3) ─ train ─ evaluate ─ keep_or_revert ──┐
   └──────────────────────────────────────── × N ────────────────────────────────────────┘
        ▼ done
```

### Skills (each a `skill.md` directory; reuse MLE-Beast's prompt content)
| skill | role |
|---|---|
| `setup` | create/activate the workspace venv and install the **ml-frameworks base stack** (torch, torchvision, torchaudio, numpy, scipy, pandas, scikit-learn, matplotlib, seaborn, pytest). Extra groups (nlp/vision/training/…) installed on demand. |
| `download_data` | identify the dataset from the task ("MNIST" → torchvision.datasets), download and stage it under the workspace, write a short `data_analysis.md`. |
| `implement` | write `model.py` / `train.py` / `evaluate.py` (+ `tests/test_smoke.py`) following the file contract; for hill-climb iterations, implement the single proposed change. |
| `verify` | **review + test in one check**: review the diff for correctness AND run the smoke tests (`pytest`); reply `ACTION: pass` or `ACTION: fail` with the reason. |
| `train` | run `train.py` (short run, e.g. 15 epochs, early stopping) writing `logs/training.log`. (Can be a `command` step, not an LLM.) |
| `evaluate` | run `evaluate.py` on the held-out split; ensure `eval_results.json` + a `Test accuracy: X` line; the metric is captured into the shared store. |
| `propose` | read `research_log.md` + current code, propose ONE experiment (HYPOTHESIS / CHANGE / RATIONALE); escalate when plateaued. |
| `keep_or_revert` | compare candidate score to `best_score`: if improved, `git add -A && git commit` and update `best_score`, reset `consecutive_failures`; else `git checkout -- . && git clean -fd` and increment `consecutive_failures`. Append to `research_log.md`. |

### `flow.yaml` sketch
```yaml
provider: { type: openrouter, model: deepseek/deepseek-v4-flash }
shared:
  task: "Build an image classifier for MNIST."
  target_accuracy: 0.98
  best_score: -1.0            # higher-is-better init (accuracy)
  consecutive_failures: 0
  max_failures: 5

workflow:
  - { id: setup,    type: agent, skill: setup }
  - { id: data,     type: agent, skill: download_data }

  # ---- baseline: implement -> verify (retry x3), then train + evaluate ----
  - id: baseline_build
    type: retry_loop
    max_iterations: 3
    action: { id: implement, type: agent, skill: implement }
    check:  { id: verify,    type: agent, skill: verify }     # review + smoke tests -> pass|fail
  - { id: baseline_train, type: command, run: "python train.py --epochs 15" }
  - id: baseline_eval
    type: agent
    skill: evaluate
    set: { best_score: "Test accuracy:\\s*([0-9.]+)" }

  # ---- hill-climb until target met or budget/plateau ----
  - id: hillclimb
    type: counting_loop
    max_iterations: 30
    exit_when: "best_score >= target_accuracy or consecutive_failures >= max_failures"
    body:
      - { id: propose, type: agent, skill: propose }
      - id: hc_build
        type: retry_loop
        max_iterations: 3
        action: { id: hc_implement, type: agent, skill: implement }
        check:  { id: hc_verify,    type: agent, skill: verify }
      - { id: hc_train, type: command, run: "python train.py --epochs 15" }
      - id: hc_eval
        type: agent
        skill: evaluate
        set: { candidate_score: "Test accuracy:\\s*([0-9.]+)" }
      - { id: keep_or_revert, type: agent, skill: keep_or_revert }
```

This already nests cleanly on today's primitives: a `retry_loop` inside a `counting_loop`
body is just a `Subflow` (a Node) chained with `>>`. `exit_when` is a plain predicate over
the shared store, so target-met **and** plateau (`consecutive_failures`) are one expression.

---

## 3. Mapping to `saage` primitives

| MLE-Beast | `saage` |
|---|---|
| implement → testing critic (retry) | `retry_loop(action=implement, check=verify)` — review+test folded into one `verify` check |
| baseline train → evaluate → record | `command`/agent steps + `set:` capture of the metric |
| hill-climb `[propose→…→eval→keep/revert] × N` | `counting_loop(body=[…], exit_when=…)` |
| convergence (target / max_steps / max_failures) | `max_iterations` + `exit_when: "best_score >= target or consecutive_failures >= max_failures"` |
| keep/revert via git | `keep_or_revert` skill using the git tools we already have |
| score finder (eval_results.json) | `set:` regex capture, with an optional `finder` agent fallback for odd metrics |

---

## 4. Engine gaps to close first

The flow above mostly runs on what we have, but a faithful, robust version needs a few
small engine additions. These are the real work items:

1. **Configurable workspace root (most important).** Today the tools are sandboxed to the
   *flow file's* directory. ML runs need a dedicated workspace (`/tmp/workspace`) for code,
   data, and checkpoints, separate from where `flow.yaml`/skills live. Add a `--workspace`
   CLI flag / `workspace:` key that sets the tool sandbox root, exposed to skills as
   `{{ workspace }}`.
2. **Reset nested-loop counters per outer iteration.** `retry_loop`'s attempt counter lives
   in `shared["_iter"][id]`; when the `hillclimb` loop re-enters `hc_build` on iteration 2,
   the stale count would make it exit immediately. Fix: reset the inner loop's counter when
   the loop is (re)entered (small change to the guard), or have `keep_or_revert` clear it.
   MLE-Beast does the equivalent ("reset `*_attempt` counters each iteration").
3. **Git as a first-class workspace concern.** `keep_or_revert` needs commit/reset/clean
   with a synthetic identity (so a fresh box without `git config` doesn't fail). MLE-Beast
   uses `git -c user.email=… -c user.name=…`; mirror that in a tiny helper or skill.
4. **(Optional) reusable step blocks.** The `implement→verify→train→evaluate` block appears
   in both the baseline and the hill-climb body. It's fine to inline with distinct ids for
   v1; a later `$ref`/anchor-with-id-rewrite would DRY it up.
5. **(Optional) richer score extraction.** A `finder` agent that reads `eval_results.json`
   handles arbitrary metric names/directions better than a single regex.

None of these are large; (1) and (2) are the only ones that genuinely block a correct run.

---

## 5. The setup skill — "install ML frameworks"

Mirror MLE-Beast's contract (`prompts/shared.py`): the workspace venv ships a **base stack**
(`torch torchvision torchaudio numpy scipy pandas scikit-learn matplotlib seaborn pytest`),
and extras install on demand. For a self-contained `saage` version the `setup` skill should:
- create a venv in the workspace (`python -m venv .venv`),
- install the base stack (pin versions; CPU vs CUDA wheel selection by `torch.cuda` probe),
- drop a `pyproject.toml` with optional groups (nlp / vision / training / …) so later steps
  can `pip install`/`poetry install -E <group>` only what they need.

This is the heaviest, most environment-dependent step (GPU detection, wheel size); worth a
`command`-based fast path plus an agent fallback.

---

## 6. File & scoring contracts (carried over verbatim from MLE-Beast)

- Code at workspace root: `model.py`, `train.py`, `evaluate.py`, (`predict.py`).
- `train.py`: argparse (`--device` auto-cuda, `--epochs` default 15, `--data-path`,
  `--checkpoint-dir`, `--lr`), 80/20 train/val split, early stopping (patience 5), writes
  `logs/training.log`, saves best checkpoint by val metric.
- `evaluate.py`: held-out split, prints `Test accuracy: 0.78`, **writes `eval_results.json`
  = the source of truth for the score.**
- `tests/test_smoke.py`: imports `model.py`, runs `--help` on the scripts.
- `research_log.md`: append-only experiment history (proposer reads it; `keep_or_revert`
  writes it).

---

## 7. Decisions (locked in)

- **Metric direction**: **user-provided.** Carry a `lower_is_better` flag (and optional
  `metric_name`) in the shared store / CLI, like MLE-Beast — so the flow handles accuracy
  (higher-better) *and* loss/RMSE (lower-better). `exit_when` reads the flag:
  `(best_score >= target if not lower_is_better else best_score <= target) or consecutive_failures >= max_failures`.
- **Train step**: an **LLM agent** that writes the training harness and runs it (adapts to
  failures), not a static `command`. The `implement` skill authors `train.py`; the `train`
  agent runs it and reacts to errors.
- **Setup depth**: **lean base stack only** for v1 (`pip install torch torchvision … pytest`);
  no poetry-extras machinery yet.
- **Mode**: **greenfield only.** Skip MLE-Beast's brownfield "existing code is the baseline"
  path entirely.

## 8. Suggested next steps
1. Land engine gaps **(1) workspace root** and **(2) nested-loop counter reset** (small PRs).
2. Scaffold `flows/greenfield_ml/` with the skills above (prompt content adapted from
   MLE-Beast), `command` train, accuracy-only scoring.
3. Smoke-test end-to-end on MNIST with a small `max_iterations`, then Fashion-MNIST.
