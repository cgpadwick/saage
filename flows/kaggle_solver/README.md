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
`final_epochs` (default 100), `hillclimb_iterations` (default 30),
`max_consecutive_failures` (default 10), `target_score` (optional early
exit), `device` (auto-detected).

## How it works

```
prepare(cmd) → hardware_probe(cmd: hardware.md) → setup(cmd: git branch + ledger)
  → comp_understanding ⇄ critic → eda ⇄ critic
  → build_baseline ⇄ pytest-smoke(cmd) → perf_review
  → short-train(cmd, timeout+measure_hw) → verify_training → record
  → data_audit
  → hillclimb ×30: propose ⇄ critic → implement ⇄ pytest(cmd) → perf_review
       → short-train(cmd, timeout+measure_hw) → verify → keep_or_revert(cmd, git)
     (exit: consecutive failures or target met)
  → final-train(cmd) → make_submission ⇄ validate(cmd) → report → grade(cmd)
```

Artifacts per run: `experiments.jsonl`, `research_log.md`,
`report.html`, `submission.csv`, git history of kept experiments.

## Medal-push machinery

- **Researcher menu** — after the baseline, a one-shot researcher writes a
  ranked menu of 8–12 ideas + anti-ideas for the problem *class*
  (`autoresearch_ideas.md`; format machine-checked by `check_ideas.py`,
  substance gated by a critic whose objections persist to
  `research_critic_feedback.md`). `propose` draws from the menu by default.
- **Portfolio rule** — every third experiment must switch model family:
  the run ends with an ensemble, and decorrelated members are what a blend
  feeds on.
- **Prediction-pool ensemble (generic)** — every scored train archives
  submission-shaped predictions (`pool_archive.py` → `ensemble_pool/`;
  contract: `predictions/{val_preds,val_labels,test_preds}.csv` +
  `score_preds.py` as a black-box metric). After the solo submission is
  validated, `blend_ensemble.py` runs Caruana greedy selection over the
  pool and replaces `submission.csv` only when the blend beats the solo
  champion on a confirmation slice the search never saw (solo kept as
  `submission_solo.csv`). Domain-blind: predictions and a scoring script
  are the only interface, so it works unchanged on any competition.

## Guards

Lessons from a live run that lost its endgame to hung single-threaded trains
(18–22h each) and phantom-OOM misdiagnoses:

- **Train timeouts** — every train step carries `timeout:` (6h short / 12h
  final). On expiry the engine kills the whole process tree and the step
  fails with exit 124; the run continues. A hung train can no longer block
  the flow or fight a retry for the GPU.
- **Measured evidence** — train steps run with `measure_hw: true`: wall time,
  GPU-utilization and load averages land in `step_metrics.<id>`, and failures
  keep a bounded stderr tail. `verify_training` reasons from these
  measurements (exit 124 = "timed out", never a guessed "OOM"), and flags
  hardware misfit (long wall, idle GPU) without failing the run.
- **perf_review** — after the baseline is built and after every experiment's
  implement, a reviewer checks the code against the box specs in
  `hardware.md` (written by `hardware_probe.py`). Blocking misfits — tensor
  work never reaching an available GPU, `set_num_threads(1)`-style pinning,
  whole-corpus single-call inference — it fixes mechanically and re-runs the
  smoke tests; softer concerns are flagged for the proposer.
- **data_audit** — one-time leakage + data-usage audit after the baseline;
  findings append to `research_log.md` where every later iteration sees them.
- **Retrieval hygiene** — `comp_understanding` and `propose` may `web_search`
  for the problem *class*, never the competition (query rules in the skills;
  every query is logged). For benchmark honesty also export
  `SAAGE_SEARCH_BLOCK_DOMAINS=kaggle.com`: with the engine's search-blocklist
  PR merged, blocked-domain results are dropped before the model sees them
  (and the var is forwarded on `saage remote` handoffs); without that PR the
  hygiene rules are prompt-only — don't publish benchmark numbers that way.
