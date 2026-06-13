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
| spooky-author-identification | deepseek-v4-flash | none | 0.3815 logloss | 0.4017 | ~$8 (4×a10, 1.6 h) | batched ×6 rounds, 2026-06-12¹ |
| spooky-author-identification | deepseek-v4-flash | none | 0.4828 logloss | 1.1152² | ~$4 (4×a6000 TC, 3 h) | sweep-up fire-and-forget ×6 rounds, 2026-06-12 |
| spooky-author-identification | deepseek-v4-flash | none | **0.3745** logloss | 0.4074 | ~$10 (4×a6000 TC, 7 h) | researcher-menu sweep ×10 rounds, 2026-06-13³ |

¹ First flow_batch.yaml outing: baseline 0.5039 → 0.3815 over 6 rounds
(18 parallel experiments); ended early on the since-raised 2-miss patience
and surfaced the no-op-implement + noise-keep bugs the guards now cover.
Bronze is ≈0.36 — within reach of a full-patience rerun.
² Thunder fire-and-forget validation run (coordinator-on-a-box, zero
laptop involvement after sweep-up). The broken test score was
train/predict skew — predict.py served near-constant probabilities;
validate_submission.py now has a deterministic variance tripwire that
fails exactly this shape and feeds the diagnosis back to the agent.
³ Full stack (researcher menu + critics + tripwire): best-ever val from
30 active experiments; healthy submission (tripwire passed on merit).
The val→test gap (0.033) is adaptive overfitting to the fixed split —
next levers: CV-based keep decisions and model ensembling (never
proposed in 10 rounds; the proposer needs an explicit nudge that
"recombine" includes combining MODELS, not just mechanisms).

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

### Batched variant (P3): K=3 parallel experiments per round

`flow_batch.yaml` keeps the same pipeline through the baseline, then each
hill-climb round proposes **3 diverse experiments at once** (one agent
call, set-level critic), runs them in parallel across registered GPU boxes
(`saage.remote.batch`: per-node data provisioning from your prepared copy,
patches back as artifacts), applies the round winner, and reproposes from
the full ledger. Run from an activated engine venv:

```bash
saage remote spawn --name w1   # ×K, or reuse registered targets
saage run flows/kaggle_solver/flow_batch.yaml --workspace /tmp/kaggle_batch \
  --set competition_id=... --set lower_is_better=... \
  --set batch_targets=w1,w2,w3
```

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
