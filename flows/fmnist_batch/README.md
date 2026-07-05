# fmnist_batch — batched parallel hill-climb (phase-2 reference flow)

The first user of the batched hill-climb machinery
(docs/batched_hillclimb_plan.md): each round, **one proposer writes K=3
deliberately-diverse experiments**, `saage.remote.batch` fans them out as
parallel runs across registered GPU boxes (phase-1 dispatch layer:
provision-once, slot scheduling, reaper), and a deterministic integrator
applies the round's winner to the workspace — which the next round's
proposals build on, informed by the full ledger including failures.

```
seed(cmd: git ws + baseline MLP)
└─ rounds ×N (counting_loop, exit: target met or 2 straight misses)
   ├─ propose_batch ⇄ propose_critic   (one agent call writes K=3;
   │                                    critic judges the SET for diversity)
   ├─ run_batch (cmd)  → python3 -m saage.remote.batch …
   │     K staged flow dirs (proposal.md each) → K parallel experiment runs
   │     (experiment.yaml: implement ⇄ smoke → train → patch) → barrier →
   │     scores from eval_results.json artifacts → BEST_* captures
   └─ integrate (cmd)  → apply winner patch, commit, ledger, exit flags
```

## First live run (2026-06-12, target val_acc 0.95, 8-epoch budget)

9 rounds × 3 experiments on 2–3 Lambda a10s ($1.29/hr each); warm rounds
took ~3–8 min wall-clock for 3 parallel experiments.

| round | winner | val_acc | also ran |
|---|---|---|---|
| – | baseline: MLP 784-256-10 | 0.879 | |
| 0 | 2-conv CNN | **0.9174** | aug-on-MLP 0.830 · SGD+cosine *crashed*¹ |
| 1 | deeper CNN + BatchNorm | **0.9275** | AdamW+cosine 0.9253 · aug 0.9042 |
| 2 | OneCycleLR schedule | **0.9371** | dropout+WD 0.9308 · residual+GAP 0.9254 |
| 3 | label smoothing | **0.9424** | wider 0.9370 · shift+erase 0.9310 |
| 4 | *(miss)* | – | resid 0.9386 · MixUp 0.9390 · drop+WD 0.9357 |
| 5 | tuned OneCycle+AdamW+clip | **0.9430** | spatial-dropout 0.9367 · TTA-flip 0.8631 |
| 6 | squeeze-excitation attention | **0.9439** | pad+crop 0.9337 · EMA 0.9293 |
| 7 | *(miss)* | – | wider 0.9431 · CutMix 0.9428 · reg 0.9406 |
| 8 | *(miss — run ends)* | – | SiLU 0.9401 · noise-aug 0.9397 · OneCycle 0.9418 |

**Final: 0.9439** (+6.5 pts over baseline; target 0.95 not reached —
saturated at the 8-epoch budget, exit on two straight misses).
Observations that matter for the kaggle port: proposals were visibly
ledger-informed (augmentation kept losing and kept getting demoted;
near-miss mechanisms were recombined onto the new base next round);
26/27 experiments completed; the 1 crash¹ was an engine bug (malformed
tool-call JSON from the model), fixed during the test — the batch layer
absorbed it as designed (nan score, round proceeded).

¹ fixed in `fix(engine): malformed tool-call JSON is the model's error`.

## Run it

```bash
# boxes (any registered ssh targets with a GPU work; spawn = Lambda)
saage remote spawn --name w1   # ×K boxes, or fewer — jobs queue on slots

# the engine venv must be on PATH (run_batch is `python3 -m saage.remote.batch`)
source .venv/bin/activate
OPENROUTER_API_KEY=... saage run flows/fmnist_batch/flow.yaml \
  --set batch_targets=w1,w2,w3

saage remote terminate w1 …    # when done — billing is on you
```

Knobs (`--set`): `target_score`, `short_epochs`, `batch_targets`; resume a
stopped climb with `--set best_score=<best> --set round_no=<next>` against
the same workspace.
