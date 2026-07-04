# nomad2018-predict-transparent-conductors — run kaggle_solver-20260704-0617-b546

outcome: medal=none above_median=false val=0.05181 test=0.5154 llm_cost=$19.7740

## Research log

# nomad2018-predict-transparent-conductors — kaggle solver research log

Goal: best the competition metric on the held-out test split (lower is better).
Submission contract: submission.csv, columns/rows must match
sample_submission.csv exactly.

Budget: every hill-climb experiment trains exactly 15 epochs
(ShortTrain) so scores are comparable; the final winner re-trains at
150 epochs (FinalTrain) before predict + submit.
The validation score that drives keep/revert comes from eval_results.json
written by train.py — never from log prose.

Every experiment below is recorded by keep_or_revert.py.
`keep` = improved the best validation score (committed); `revert` = did not.

## Experiments

## Experiment 1 — KEPT ✅ (candidate=0.0603123, best=0.0603123)
- changed: cache/xyz_features_full.csv, cache/xyz_sample.csv, competition_understanding.md, eda_baseline.py, eda_correlations.py, eda_distributions.py, eda_overview.py, eda_xyz_analysis.py, eda_xyz_full.py, eda_xyz_full_analysis.py, eda_xyz_verify.py, memory/nomad2018-predict-transparent-conductors.md, memory/spooky-author-identification.md, model.py, predict.py, tests/test_smoke.py, train.py
- commit: 1b2d43c0

(no summary written)

## Data audit (after baseline)
LEAKAGE: none found
- FeatureStats fit on training rows only (train.py:115, model.py:357)
- XYZ parsing is deterministic per-row, no fit step; no target-encoding anywhere
- StratifiedKFold on (spacegroup, atom_count) gives disjoint train/val per fold
- predict.py reads test CSV+XYZ, train.py never touches test files
UNUSED DATA: minor — XYZ Cartesian lattice vectors and atomic positions
- XYZ files contain 3 full Cartesian lattice vectors; only their scalar volume is kept (model.py:194-214). The orientation matrix and any off-diagonal info lost to CSV's lv+angle summary is discarded
- Per-atom coordinates (3D positions of Al/Ga/In/O) are read (model.py:115-150) but only the mean cation-O distance is computed; nearest-neighbor std, polyhedral distortion, coordination numbers, full bond-length histograms, and inter-cation distances are all unused
- All CSV columns and all 2,400 XYZ files are otherwise wired in (train+test)
OPPORTUNITY:
- Re-derive lattice angles/vectors from XYZ lattice vectors as a cross-check (and pick the higher-precision source per row); also pull the 3 Cartesian lattice vectors' off-diagonal terms as 6 new scalars — XYZ is the ground truth for the cell geometry
- Stronger XYZ features: per-element coordination numbers (Al-/Ga-/In-coordination by O), O-O nearest-neighbor stats, and the std of the cation-O bond lengths (model.py already computes means). Past-run note warns against dumping 20+ XYZ features; stay at ≤5 new well-motivated scalars

## Experiment 2 — KEPT ✅ (candidate=0.060078, best=0.060078)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py
- commit: 12c79913

Add three cation cross-product scalars (al·ga, al·in, ga·in) to `engineered_features` in `model.py` (51→54 features). The `sg_x_cation` block — the ablation's highest-leverage component (+0.0117 when dropped) — currently models only linear cation effects, so these three products supply the missing second-order basis for the non-linear bandgap bowing in Al/Ga/In mixing. Expected: ~0.0005–0.0015 RMSLE improvement on the 5-fold OOF (current 0.0603123).

## Experiment 3 — KEPT ✅ (candidate=0.0597876, best=0.0597876)
- changed: model.py
- commit: 4c24387f

Add 12 per-spacegroup cation-difference cross terms (`sg{sg}_x_al_minus_in`, `sg{sg}_x_al_minus_ga` for 6 SGs) to the `sg_x_cation` block in `model.py`, growing it 18→30 columns (total features 54→66). The hypothesis is that the (al−in) and (al−ga) axes are the physics-grounded per-SG bandgap/formation-energy signals (r(al−in, bandgap) = 0.82–0.97 in every SG) the model must currently re-derive from 3 raw cation columns in its 64-unit ReLU. Expected to cut 5-fold OOF RMSLE by ~0.0005–0.0015 from the current 0.0600780.

## Experiment 4 — KEPT ✅ (candidate=0.0594006, best=0.0594006)
- changed: model.py
- commit: 8d61745f

Add 12 per-SG geometric cross terms (sg{sg}_x_lv3, sg{sg}_x_cos_beta, 6 SGs) to model.py's sg_x_cation block (30→42 cols). Hypothesis: these are the strongest per-SG geometric signals (mean |r| 0.39 FE, 0.79 BG) with sign-flips across SGs, so explicit products beat the trunk re-deriving a 6-DOF basis from ~250 samples/SG; expected −0.0003 to −0.0010 RMSLE, matching KEPT Exp 3 on the geometric axis.

## Experiment 5 — KEPT ✅ (candidate=0.0587166, best=0.0587166)
- changed: model.py
- commit: b16194b6

Add 12 per-spacegroup geometric cross terms (`sg{sg}_x_lv2` and `sg{sg}_x_cos_gamma`, 6 SGs × 2) to the `sg_x_cation` block in model.py, growing it 42 → 54 columns. This extends the same `sg × X` design pattern that the three prior KEPT experiments (Exp 2/3/4, RMSLE 0.0603123 → 0.0597876 → 0.0594006) successfully applied, now targeting the next two strongest per-SG geometric features by Pearson |r| ranking (b-axis length and γ-angle, both showing sign-flipping or per-SG variation that a single global column cannot resolve). Hypothesis: same dose, same architecture as Exp 4, expected −0.0002 to −0.0006 RMSLE.

## Experiment 6 — KEPT ✅ (candidate=0.0581132, best=0.0581132)
- changed: model.py
- commit: 868cb443

Add 12 per-spacegroup geometric cross terms (`sg{sg}_x_cos_alpha` and `sg{sg}_x_lattice_vector_1_ang` for 6 SGs) to the `sg_x_cation` block in `model.py:build_features`, growing it 54→66 columns. Hypothesis: these are the next two features in the per-SG |r| ranking after the four geometric pairs Exps 4–5 already KEPT, with the same +768-trunk-weight dose and a similar −0.0003 to −0.0008 RMSLE payoff expected.

## Experiment 7 — reverted ❌ (candidate=0.0581301, best=0.0581132)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py, predict.py, tests/test_smoke.py, train.py

Add a 6×2 learnable per-SG intercept (12 params, init 0) to BaselineMLP, projected through the existing sg_oh one-hot to per-target output, bypassing the trunk. The highest-leverage sg_x_cation block (66 cols) currently reconstructs the per-SG mean from multiple cross-terms; a dedicated intercept exposes this 12-DOF offset in one step, freeing trunk capacity for per-SG gradients. Expected: −0.0003 to −0.0010 5-fold OOF RMSLE.

## Experiment 8 — reverted ❌ (candidate=0.0591911, best=0.0581132)
- changed: model.py

Add 6 per-SG `ga - in` cross terms to the `sg_x_cation` block in `model.py` (66→72 cols), completing the cation-difference basis (block already has `al-in` and `al-ga`). The `ga-in` axis has the strongest per-SG correlation of any missing cation feature (combined |r| 1.034) and unique formation-energy signal. Algebraically redundant with existing columns, the new column gives the ReLU MLP a separately-initialized weight. Expected RMSLE: −0.0003 to −0.0008.

## Experiment 9 — reverted ❌ (candidate=0.0590081, best=0.0581132)
- changed: model.py

Add 6 per-spacegroup 3-way product columns `sg{sg}_x_al_minus_in_x_lattice_vector_3_ang` to the `sg_x_cation` cross-term block, growing it from 66 to 72 columns (no other changes). The two prior reverts failed because the added columns were linear combinations of the existing 66 cols (row-space redundancy); a 3-way product is outside that row-space, so the 1-hidden-layer ReLU trunk cannot synthesize it cheaply, and it carries the strongest per-SG combined |r| signal in the dataset (mean 1.257, 21% above the 2nd-place product). Expected delta: −0.0003 to −0.0008 RMSLE on the 5-fold OOF (current best 0.0581132), at a conservative +6-col dose (half the prior +12).

## Experiment 10 — reverted ❌ (candidate=0.0589296, best=0.0581132)
- changed: model.py

After 3 consecutive reverts on `sg_x_cation` (per-SG intercept +0.000017, per-SG `ga-in` +0.0011, per-SG `al-in × lv3` 3-way product +0.0009 — spanning non-feature, linearly-redundant, and 3-way-product subclasses), pivot to the ablation's runner-up `engineered_features` block
… (truncated)
