# lewm_hillclimb — brownfield hill-climb on LeWorldModel (OGBench-Cube)

A brownfield variant of `greenfield_ml`: instead of building an ML pipeline from
scratch, this flow operates on the **existing le-wm repository**
(`/home/cpadwick/code/le-wm`) and hill-climbs its training configuration until
OGBench-Cube planning success_rate approaches the paper's **74%**
(LeWorldModel paper, Fig. 6; prior manual runs here scored 60–64%).

## What it does

1. **setup** — switches the le-wm repo to a `saage-hillclimb` branch, commits a
   snapshot of the current tracked changes, protects run artifacts
   (checkpoints, hydra outputs, …) via `.git/info/exclude`, and seeds
   `research_log.md` with the paper's reference hyperparameters and history.
2. **baseline** — trains the *current* config for the fixed budget
   (`train_epochs`, default 8) and evaluates it with the frozen protocol
   (`eval.py --config-name=cube.yaml`, 50 episodes, seed 42) to seed
   `best_score`. Comparing experiments to a same-budget baseline is what makes
   the hill-climb fair.
3. **hillclimb** (up to 10 iterations, exits early at `target_success`):
   - `propose` → `proposal_critic` (cheap vetting before a ~20 h train run)
   - `implement` → `verify` (applies the change; smoke-tests a 2-batch train)
   - deterministic `train` → `eval` → `keep_or_revert` (git keeps/reverts the
     change; the best checkpoint is promoted to
     `$STABLEWM_HOME/checkpoints/lewm_cube_best`)
4. **report** — writes and commits `report_narrative.md` in the le-wm repo.

The LLM only writes proposals and edits config/model code. Training, eval,
scoring, and keep/revert are deterministic commands.

## Cost expectations

On this machine one epoch ≈ **2.5 h**, so each experiment (8 epochs + ~10 min
eval) ≈ **20 h**. A full 1 baseline + 10 iteration run is therefore on the
order of 9 days of GPU time; the loop exits early if the target is reached.
Tune with `--set train_epochs=N` and `--set target_success=X`.

## Run

```bash
cd ~/code/saage && source .venv/bin/activate   # or however saage is installed
export OPENROUTER_API_KEY=sk-or-...
# long-lived: run under tmux or nohup
saage run contrib/lewm_hillclimb/flow.yaml
```

Useful overrides:

```bash
saage run contrib/lewm_hillclimb/flow.yaml \
  --set train_epochs=8 \
  --set target_success=74.0
```

## Notes / design decisions

- **`STABLEWM_HOME` is baked into every train/eval command**
  (`stablewm_home: "$HOME/.stable-wm"` in shared, overridable with `--set`).
  Without it, stable_worldmodel defaults to `~/.stable_worldmodel`, misses the
  local datasets, and tries to download `ogbench/cube_single_expert.h5` from
  HuggingFace (which 401s). The run therefore does not depend on the
  launching shell exporting the variable.

- **Frozen eval**: agents are forbidden (skill rules + critic + verify diff
  check) from touching `eval.py` or `config/eval/`. The flow pins
  `solver.n_steps=10` on the eval command — the paper's CEM budget for cube
  (App. D; the repo default of 30 is the PushT setting). Note the user's
  earlier 60–64% scores were measured at n_steps=30, so they are not directly
  comparable to hill-climb scores.
- **Fixed budget**: every experiment trains exactly `train_epochs` epochs
  (`trainer.max_epochs` override); the proposer is steered toward changes that
  pay off in a short budget (lr/schedule, batch size, SIGReg λ, dropout, …).
- **wandb/trackio disabled** (`wandb.enabled=False`) for harness runs so a
  logging hiccup can never kill a 20 h experiment. Re-enable in `flow.yaml` if
  you want trackio dashboards per experiment.
- **Checkpoint hygiene**: `clean_ckpt.py` wipes `lewm_cube_exp` /` lewm_smoke`
  dirs before each run (train.py auto-resumes otherwise); it refuses to touch
  anything but the saage-owned names, so `lewm_cube`, `lewm`, `lewm_reacher`
  are safe.
- **Failure-proof scoring**: `candidate_score` is reset to `-1` before every
  train; if train/eval crashes, the experiment reads as -1 and is reverted.
- The user's prior best checkpoint (`lewm_cube/weights_epoch_54.pt`, 60–64%)
  is left untouched; it is NOT the hill-climb baseline because it used a
  different (much longer) budget.
