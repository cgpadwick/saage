# nomad2018-predict-transparent-conductors — run kaggle_solver-20260703-1603-d093

outcome: medal=none above_median=false val=0.05178 test=0.1424 llm_cost=$7.2623

## Research log

# nomad2018-predict-transparent-conductors — kaggle solver research log

Goal: best the competition metric on the held-out test split (lower is better).
Submission contract: submission.csv, columns/rows must match
sample_submission.csv exactly.

Budget: every hill-climb experiment trains exactly 15 epochs
(ShortTrain) so scores are comparable; the final winner re-trains at
100 epochs (FinalTrain) before predict + submit.
The validation score that drives keep/revert comes from eval_results.json
written by train.py — never from log prose.

Every experiment below is recorded by keep_or_revert.py.
`keep` = improved the best validation score (committed); `revert` = did not.

## Experiments

## Experiment 1 — KEPT ✅ (candidate=0.0673221, best=0.0673221)
- changed: competition_understanding.md, data_analysis.md, eda_correlations.py, eda_overview.py, eda_plots.py, eda_xyz.py, model.py, plots/bandgap_vs_al_by_spacegroup.png, plots/composition_targets.png, plots/feature_correlations.png, plots/target_distributions.png, predict.py, tests/test_smoke.py, train.py
- commit: 65c102d7

(no summary written)

## Experiment 2 — reverted ❌ (candidate=0.0703947, best=0.0673221)
- changed: model.py, predict.py, train.py

Added ~28 structural fingerprint features from XYZ geometry files (bond-length statistics for 6 element pairs, coordination numbers for Al/Ga/In, and octahedral distortion metrics) to the tabular feature matrix. This captures local bonding environments and polyhedral distortions that directly determine bandgap and formation energy but are invisible to composition-only features, aiming to significantly reduce RMSLE beyond the tabular-only baseline.

## Experiment 3 — reverted ❌ (candidate=0.0673221, best=0.0673221)
- changed: model.py, predict.py, train.py

Experiment 3 replaces the 3-layer MLP with XGBoost multi-output regression (default params: max_depth=6, learning_rate=0.05, 2000 trees with early stopping) to see if gradient-boosted trees better capture spacegroup×composition interaction effects on 2160 samples without overfitting, after the structural-feature experiment (Exp 2) raised validation RMSLE from 0.0673 to 0.0704.

## Experiment 4 — reverted ❌ (candidate=0.0673221, best=0.0673221)
- changed: model.py, predict.py, tests/test_smoke.py, train.py

Adding ~12 XYZ-derived structural features (mean cation–O bond lengths, coordination numbers, octahedral distortion) to the existing 53 tabular features, and switching from MLP to XGBoost with conservative regularization. The hypothesis is that atomic positions encode bonding environments tabular features miss, while XGBoost's built-in regularization avoids the overfitting that sank Exp 2 when structural features were given to the MLP.

## Experiment 5 — KEPT ✅ (candidate=0.0646707, best=0.0646707)
- changed: model.py
- commit: 63847d5e

Replaced the 3-hidden-layer MLP (256→128→64, ~105K params, with BatchNorm) with a single 32-unit hidden layer (~1.8K params, no BatchNorm, Dropout retained) in model.py. The hypothesis is that the overparameterized network overfits on only 2,160 samples and cannot converge within the 15-epoch budget, while a simpler model will generalize better and converge faster.

## Experiment 6 — reverted ❌ (candidate=0.0668551, best=0.0646707)
- changed: train.py

Replace ReduceLROnPlateau (patience=5) with CosineAnnealingLR (T_max=15, eta_min=1e-6) in train.py. The current scheduler's 5-epoch patience rarely triggers a reduction within the 15-epoch budget, so the model trains at lr=0.001 almost the entire time; cosine annealing's full cycle from high to low LR helps escape sharp minima and converge faster in limited epochs.

## Experiment 7 — KEPT ✅ (candidate=0.0605497, best=0.0605497)
- changed: model.py
- commit: fec7c4b3

Increased the single hidden layer from 32 to 64 units (1,760→3,520 params) to test whether the current bottleneck underfits the 53 engineered features, especially composition×spacegroup interactions. The 64-unit layer is still 30× smaller than the 105K-parameter model that previously overfit, so the risk is minimal while the potential for better fit is real.

## Experiment 8 — reverted ❌ (candidate=0.0614808, best=0.0605497)
- changed: model.py

Doubled the single hidden layer from 64 to 128 units (3.6K→7.2K params) to test whether the MLP remains capacity-constrained, following the 32→64 doubling that yielded a 6.4% RMSLE improvement. Still 7.7× smaller than the overfit 55K-param original, so overfitting risk stays low.

## Experiment 9 — reverted ❌ (candidate=0.0633848, best=0.0605497)
- changed: model.py

Adds a linear residual (skip) connection from the 53-dim input directly to the 2-dim output in the 64-unit MLP. This lets the skip path absorb strong composition→bandgap linear correlations (e.g., percent_atom_in r=−0.76) so the hidden layer can specialize on non-linear spacegroup×composition interactions without needing extra parameters that invite overfitting.

## Experiment 10 — reverted ❌ (candidate=0.0629818, best=0.0605497)
- changed: train.py

Replace ReduceLROnPlateau scheduler (patience=5, rarely fires within 15 epochs) with OneCycleLR — a warmup-then-cosine-anneal schedule designed for super-convergence in limited-budget training. This aims to improve final RMSLE by enabling better exploration early (30% warmup phase) and fine-grained convergence later, without changing model architecture or features.

## Experiment 11 — KEPT ✅ (candidate=0.060114, best=0.060114)
- changed: model.py, predict.py, train.py
- commit: cc9e9076

Adding three mean cation–oxygen bond length features (Al–O, Ga–O, In–O) parsed from XYZ geometry files to the existing 53 tabular features (56 total, +195 params). This aims to improve validation RMSLE (currently 0.06055) because local bonding environment directly determines bandgap and formation energy and is not fully captured by lattice parameters, spacegroup, and composition alone.

## Experiment 12 — reverted ❌ (candidate=0.0618947, best=0.060114)
- changed: model.py

Added three cation–O bond length standard deviation features (Al–O, Ga–O, In–O) as octahedral distortion metrics (features: 56→59). The hypothesis is that distorted octahedral environments produce different bandgap and formation energy signals than regular octahedra—information not captured by mean bond length alone—improving the current validation RMSLE of 0.06011.

## Experiment 13 — KEPT ✅ (candidate=0.0598068, best=0.0598068)
- changed: model.py
- commit: dd988685

Exp 13 changes the loss function from a pooled RMSLE (which implicitly overweights bandgap) to a per-target mean-of-MSLE that matches the competition metric's equal-weight-per-column structure. The hypothesis is that aligning training loss with the true evaluation metric will improve validation RMSLE by forcing the model to balance accuracy across both targets rather than focusing disproportionately on bandgap.

## Experiment 14 — reverted ❌ (candidate=0.0617075, best=0.0598068)
- changed: model.py

Replace the shared 64→2 output head with two per-target 64→1 heads in the MLP's MultiOutputRegressor (zero parameter increase). Since formation energy and bandgap have different physical determinants and only r=−0.45 correlation, a single weight matrix forces representational compromise; separate heads let each target learn its own feature importance patterns without interference.

## Experiment 15 — reverted ❌ (candidate=0.062183, best=0.0598068)
- changed: model.py

Add BatchNorm1d (affine, 128 params) after the 64-unit hidden linear layer, before ReLU and Dropout(0.2), to stabilize training by reducing internal covariate shift across 15 epochs. This restores BatchNorm removed in the simplification to a single hidden layer (Exp 5), combining small capacity with improved optimization dynamics — no other hyperparameters or architectural changes.

## Experiment 16 — KEPT ✅ (candidate=0.0593045, best=0.0593045)
- changed: model.py
- commit: 86c34487

Removes nn.Dropout(0.2) from the hidden layer
… (truncated)
