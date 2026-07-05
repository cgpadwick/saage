# spooky-author-identification — run kaggle_solver-20260705-1638-a123

outcome: medal=none above_median=false val=0.3663 test=0.5713 llm_cost=$0.8904

## Research log

# spooky-author-identification — kaggle solver research log

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

## Experiment 1 — KEPT ✅ (candidate=19.617, best=19.617)
- changed: competition_understanding.md, data_analysis.md, memory/nomad2018-predict-transparent-conductors.md, memory/spooky-author-identification.md, model.py, predict.py, tests/test_smoke.py, train.py
- commit: 0208dc3f

(no summary written)

## Experiment 2 — KEPT ✅ (candidate=0.554884, best=0.554884)
- changed: model.py, predict.py, tests/test_smoke.py, train.py
- commit: 8ce77635

(no summary written)

## Data audit (after baseline)
LEAKAGE: none found
UNUSED DATA: all data used — train.csv (id, text, author), test.csv (id, text), sample_submission.csv are all used by the pipeline.
OPPORTUNITY: the only unused signal is **description.md** (no model-usable features). No additional modalities exist in this dataset (pure NLP competition, only text + labels).


## Round 0 (2026-07-05T17:25Z)

- p0: Full sparse TF-IDF + LogisticRegression (no SVD, no MLP) [model family — 0.5679 (done)
- p1: Enhanced stylometric features from EDA findings [feature representatio — 0.5679 (done)
- p2: Post-hoc temperature scaling of MLP probabilities [regularization] — 0.55327 (done)

**Verdict:** KEPT p2 (Post-hoc temperature scaling of MLP probabilities [regularization]) — new best 0.55327

## Round 1 (2026-07-05T17:46Z)

- p0: Replace MLP with LogisticRegression on SVD+stylo features [model famil — 0.49871 (done)
- p1: Label smoothing via PyTorch MLP [regularization] — 0.51564 (done)
- p2: Weighted ensemble: MLP(SVD) + LR(sparse) + LR(char-only) [ensembling] — 0.51929 (done)

**Verdict:** KEPT p0 (Replace MLP with LogisticRegression on SVD+stylo features [model famil) — new best 0.49871

## Round 2 (2026-07-05T18:20Z)

- p0: GloVe 300d TF-IDF-weighted average embeddings [feature representation] — 0.50509 (done)
- p1: 5-fold cross-validation stacking with LR meta-learner [ensembling] — 0.39976 (done)
- p2: Iterative hard-example reweighting via focal-loss-inspired sample weig — 0.49811 (done)

**Verdict:** KEPT p1 (5-fold cross-validation stacking with LR meta-learner [ensembling]) — new best 0.39976

## Round 3 (2026-07-05T18:40Z)

- p0: Expand char n-gram range and max_features in stacking base models [fea — 0.3979 (done)
- p1: Enhanced stylometric features in stacking Model A [feature representat — 0.39946 (done)
- p2: GloVe 300d TF-IDF-weighted average as 3rd stacking base model [feature — 0.39976 (done)

**Verdict:** KEPT p0 (Expand char n-gram range and max_features in stacking base models [fea) — new best 0.3979

## Round 4 (2026-07-05T19:05Z)

- p0: Orthogonalize stacking base models: char-only vs word-only [feature re — 0.37904 (done)
- p1: Calibrated LinearSVC as 3rd stacking base model for algorithmic divers — 0.3837 (done)
- p2: Inject EDA-discovered goldmine features into stylometrics [feature rep — 0.3979 (done)

**Verdict:** KEPT p0 (Orthogonalize stacking base models: char-only vs word-only [feature re) — new best 0.37904

## Round 5 (2026-07-05T19:26Z)

- p0: Add ComplementNB as a 3rd stacking base model [model family] — 0.37904 (done)
- p1: Add case-sensitive character TF-IDF to Model A [feature representation — 0.37904 (done)
- p2: Per-base-model temperature scaling before meta-learner [regularization — 0.37904 (done)

**Verdict:** round best 0.37904 did not beat 0.37904

## Round 6 (2026-07-05T19:59Z)

- p0: Per-class calibrated convex blend meta-learner [regularization] — 0.38684 (done)
- p1: Character-level 1D CNN as 3rd stacking base model (budget-compliant) [ — 0.37904 (done)
- p2: Raw-count char n-gram model as 3rd stacking base model [feature repres — 0.37882 (done)

**Verdict:** KEPT p2 (Raw-count char n-gram model as 3rd stacking base model [feature repres) — new best 0.37882

## Round 7 (2026-07-05T20:43Z)

- p0: Enhanced EDA goldmine stylometric features [feature representation] — 0.37831 (done)
- p1: Weighted ensemble: stacking + sparse TF-IDF+LR with optimized blend [e — 0.36654 (done)
- p2: Full sparse char+word TF-IDF + LogisticRegression replacing stacking [ — 0.36794 (done)

**Verdict:** KEPT p1 (Weighted ensemble: stacking + sparse TF-IDF+LR with optimized blend [e) — new best 0.36654

## Round 8 (2026-07-05T21:22Z)

- p0: Enhanced EDA stylometric features in WeightedEnsemble [feature represe — 0.3674 (done)
- p1: 2-model stacking: meta-LR on OOF predictions from both sub-models [ens — FAILED (timeout)
- p2: Per-class OvR LogisticRegression replacing multinomial in SparseLRMode — 0.36652 (done)

**Verdict:** round best 0.36652 did not beat 0.36654

## Round 9 (2026-07-05T21:49Z)

- p0: PyTorch learned word embedding as 4th stacking base model [model famil — 0.36632 (done)
- p1: Per-class blend weights for WeightedEnsemble [ensembling] — 0.36629 (done)
- p2: Label-smoothed PyTorch meta-learner replacing sklearn LR [regularizati — 0.36654 (done)

**Verdict:** KEPT p1 (Per-class blend weights for WeightedEnsemble [ensembling]) — new best 0.36629
