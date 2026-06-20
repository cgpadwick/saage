# Autoresearch ideas: pushing LeWM past 76% on OGBench-Cube

Curated 2026-06-11 from the LeWM paper's own ablations
([arXiv:2603.19312](https://arxiv.org/html/2603.19312v1)), the current
training config, and the world-model literature. The proposer should treat
this as a ranked menu: prefer the highest-ranked UNTRIED idea, cross-checking
research_log.md; deviating is allowed with explicit justification.

**Hard constraints (do not violate):**
- The eval protocol is FROZEN (eval.py, 50 episodes, seed 42, CEM
  solver.n_steps=10). ALL ideas must be train-side. Planning/eval-side
  changes are invalid experiments.
- Budget is FIXED at 8 epochs/experiment — prefer changes that help at short
  budget. batch_size stays 128 (paper recipe; a prior local bs-256 deviation
  was reverted for cause).
- Context: baseline 76.0 was achieved with exactly the current repo config.
  Paper reports LeWM 74, but DINO-WM hits 86 on cube ("higher visual
  complexity" is the paper's explanation) — the visual/encoder side is the
  known weakness; 86 is the ambition line.

## Ranked ideas

### 1. Multi-step rollout loss (num_preds: 1 → 2, then 3)
`config/train/lewm.yaml: num_preds`. The single biggest mismatch between
training and use: training predicts ONE step (teacher-forced) but CEM plans
over horizon 5 with frameskip 5 — compounding error in rollouts is the
classic failure (the standard fix across the world-model literature is to
add a multi-step/rollout term to the loss; e.g. V-JEPA-2-style training
combines teacher-forcing with rollout loss). The config already exposes
`num_preds`; check `jepa.py`/`module.py` honor it (if the loss only uses
prediction 1, wire the extra predictions into the next-embedding MSE,
averaged). Cost: ~linear in num_preds for the predictor pass only (encoder
unchanged) — modest. Try num_preds=2 first; if kept, a later experiment can
try 3.

### 2. Predictor capacity: depth 6 → 12 (the paper's strongest ablation)
`config/train/model/lewm.yaml: predictor.depth`. Paper Tab. 6 (PushT):
predictor tiny 80.7 / small 96.0 / base 86.7 — a +15-point swing from
capacity, with a clear peak at "small". The current predictor (depth 6,
heads 16, mlp 2048) sits at/near the lower setting. Doubling depth to 12 is
the directional move toward "small"; keep heads/mlp as-is first (one change
at a time). Cost: predictor FLOPs roughly double, but the encoder dominates
wall-clock — expect <30% epoch-time increase.

### 3. Cosine LR schedule + warmup
`optimizer` block (likely needs a small code change in train.py/module.py to
add a scheduler — lightning supports it via configure_optimizers). Current:
constant lr 5e-5, no warmup, no decay. At an 8-epoch budget, cosine decay to
~lr/10 with ~500-step linear warmup is among the most reliable free wins in
ViT-family training. Zero extra cost.

### 4. Encoder capacity: vit tiny → small (the DINO-WM gap hypothesis)
`config/train/model/lewm.yaml: encoder.size: tiny → small`. The paper's own
explanation for losing to DINO-WM on cube is VISUAL complexity, and the
encoder is the visual bottleneck. embed_dim is read from the encoder config —
keep `embed_dim: 192` consistency in mind: vit-small is 384-dim, so
`embed_dim` must change to 384 too (predictor/projector dims follow ${embed_dim}).
EXPENSIVE: roughly 2x epoch time (~11h/experiment) — burn one experiment on
this only after cheaper wins are banked, and say so in the proposal.

### 5. Longer context: history_size 3 → 4
`config/train/lewm.yaml: history_size`. The AR predictor conditions on 3
frames (with frameskip 5 ≈ 15 env steps of context). Cube manipulation has
occlusion/ambiguity; +1 frame of history is cheap (~10-15% step cost) and
directly increases the predictor's information.

### 6. Optimizer tuning: weight_decay 1e-3 → 0.05
`optimizer.weight_decay`. ViT-family training conventionally uses wd
0.03-0.1 with AdamW; 1e-3 is very light. At short budgets regularization via
wd often beats dropout increases (dropout is already at the paper's 0.1
optimum — do NOT touch dropout, Tab. 9 shows 0.2 hurts).

### 7. More training data: train_split 0.9 → 0.97
`config/train/lewm.yaml: train_split`. The val split only feeds monitoring,
not the frozen eval; reclaiming 7% more trajectories is free signal. Tiny
but riskless. Combine with another small change only if the log shows
several single-change experiments already banked.

### 8. Mild appearance augmentation (color jitter ± small crop)
Data pipeline change (wherever frames are transformed — check
stable_worldmodel data transforms). JEPA-style models can benefit from mild
augmentation invariance, but it can also fight the next-embedding objective
(augmentation must be CONSISTENT across the history+target frames of one
sample — augment per-trajectory, not per-frame). Medium risk, medium reward;
implement carefully or skip.

### 9. SIGReg weight probes: 0.09 → 0.05 / 0.15
`loss.sigreg.weight`. The paper tuned 0.09 as the peak on their setting
(0.01-0.2 all workable, 0.5 collapses); knots/num_proj are explicitly
insensitive (don't touch). Only worth one probe late in the run if the log
shows everything else exhausted.

## Anti-ideas (documented so the proposer doesn't waste budget)
- embed_dim alone (saturates above ~184 per paper Fig. 15 — only moves as a
  consequence of encoder size, idea 4).
- batch_size changes (prior local deviation reverted; lr was never rescaled).
- dropout changes (0.1 is the measured optimum; 0.2 measured worse).
- SIGReg knots/projections (measured insensitive).
- eval/planning-side anything (frozen protocol).
- reconstruction loss (paper ablation: hurts).

## Sources
- [LeWorldModel paper](https://arxiv.org/html/2603.19312v1) — Tab. 6
  (predictor capacity), Tab. 9 (dropout), Fig. 15 (embed_dim), Fig. 16
  (SIGReg λ), App. D/E (cube recipe), baselines (DINO-WM 86 on cube).
- [LeJEPA](https://arxiv.org/pdf/2511.08544) — SIGReg background; no-EMA
  design intent (don't add EMA back).
- [FF-JEPA](https://arxiv.org/html/2606.09311v1) /
  [V-JEPA 2 review](https://artgor.medium.com/paper-review-v-jepa-2-self-supervised-video-models-enable-understanding-prediction-and-planning-28410d8a1c6b)
  — compounding error & teacher-forcing+rollout-loss training.
