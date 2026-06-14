# kaggle_solver — autonomous Kaggle competitions on saage

A hill-climbing Kaggle solver as a saage flow: understand the competition,
EDA, build a baseline, then propose→implement→train→keep-or-revert until the
budget is spent — finishing with a full-budget train, a validated
`submission.csv`, a research narrative, and an `mlebench` grade.

Ported from mle-beast's benchmark pipeline
([plan + competitive analysis](../../docs/kaggle_solver_plan.md)); the
actor/critic loops become saage `retry_loop`s, and everything mechanical is
deterministic: pytest smoke checks and submission validation route the loops
via command `ACTION:` output, and the score that drives keep/revert comes
from the `train.py` → `eval_results.json` contract, never from LLM
log-reading.

## Results

| competition | model | medal | val score | test score | cost | run |
|---|---|---|---|---|---|---|
| _(results land here as the benchmark sweeps run — M1/M2/M3)_ | | | | | | |

**The brag we're building toward:** medals per dollar — deepseek-class
models on $0.35–1.99/hr rented GPUs, reproducible from this YAML.

## Run it

```bash
# one-time: competition data (needs Kaggle creds + accepted rules; py>=3.11)
pip install "saage[kaggle-solver]"
export KAGGLE_API_TOKEN=$(cat ~/.kaggle/access_token)
mlebench prepare -c spooky-author-identification --data-dir ~/.mlebench/data

# the run
OPENROUTER_API_KEY=... saage run flows/kaggle_solver/flow.yaml \
  --workspace /tmp/kaggle_run \
  --set competition_id=spooky-author-identification \
  --set lower_is_better=true        # multiclass logloss

# or hand it off to a rented GPU box
saage remote handoff flows/kaggle_solver/flow.yaml --target <node> \
  --set competition_id=... --set lower_is_better=...
```

Key knobs (`--set`): `short_epochs` (per-experiment budget, default 15),
`final_epochs` (default 100), `max_consecutive_failures` (default 10),
`target_score` (optional early exit), `device` (auto-detected).

## How it works

```
prepare(cmd) → setup(cmd: git branch + ledger)
  → comp_understanding ⇄ critic → eda ⇄ critic
  → build_baseline ⇄ pytest-smoke(cmd)
  → short-train(cmd) → verify_training → record
  → hillclimb ×30: propose ⇄ critic → implement ⇄ pytest(cmd)
       → short-train(cmd) → verify → keep_or_revert(cmd, git)
     (exit: consecutive failures or target met)
  → final-train(cmd) → make_submission ⇄ validate(cmd) → report → grade(cmd)
```

Artifacts per run: `experiments.jsonl`, `research_log.md`,
`report_narrative.md`, `submission.csv`, git history of kept experiments.
