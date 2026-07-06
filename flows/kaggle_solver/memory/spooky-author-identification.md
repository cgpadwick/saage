# spooky-author-identification — run kaggle_solver-20260705-2239-fedd

outcome: medal=none above_median=true val=0.3973 test=0.4188 llm_cost=$0.8608

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

## Round 1 (2026-07-05T23:34Z)

- p0: Two-layer stacking ensemble with orthogonal base models [ensembling] — 0.42156 (done)
- p1: Chi-squared feature selection on combined char+word TF-IDF + SGDClassi — 0.56291 (done)
- p2: Temperature scaling for probability calibration [optimization & schedu — 0.44239 (done)

**Verdict:** round best 0.42156 did not beat 0.56287

## Round 2 (2026-07-06T00:34Z)

- p0: Stylometric feature vector augmentation on char+word TF-IDF + SGDClass — 0.77018 (done)
- p1: Label smoothing via label-noise SGD, fix vectorizer leak [optimization — 0.59854 (done)
- p2: L2-regularized LogisticRegression with balanced class weights on char+ — 0.56291 (done)

**Verdict:** round best 0.56291 did not beat 0.56287

## Round 3 (2026-07-06T00:54Z)

- p0: Char-only TF-IDF + LogisticRegression with L2 [feature representation] — 0.56291 (done)
- p1: Complement Naive Bayes as primary classifier [model family] — 0.44463 (done)
- p2: Confidence-weighted convex blend of three orthogonal sub-models [ensem — 0.4736 (done)

**Verdict:** KEPT p1 (Complement Naive Bayes as primary classifier [model family]) — new best 0.44463

## Round 4 (2026-07-06T01:13Z)

- p0: GloVe-enhanced LogisticRegression with char+word TF-IDF [feature repre — 0.45105 (done)
- p1: Temperature scaling on current best ComplementNB [optimization & sched — 0.44463 (done)
- p2: Char-only TF-IDF + ComplementNB (remove word features) [feature repres — 0.55137 (done)

**Verdict:** round best 0.44463 did not beat 0.44463

## Round 5 (2026-07-06T01:20Z)

- p0: Expand char n-grams to (2,7) with more features, remove sublinear_tf [ — 0.43512 (done)
- p1: Stylometric features added to TF-IDF + ComplementNB [feature represent — 0.44463 (done)
- p2: Remove word bigrams — word unigrams only [regularization] — 0.45222 (done)

**Verdict:** KEPT p0 (Expand char n-grams to (2,7) with more features, remove sublinear_tf [) — new best 0.43512

## Round 6 (2026-07-06T01:39Z)

- p0: LogisticRegression on current best features [model family] — 0.43512 (done)
- p1: Multi-channel: raw-count char features + TF-IDF word features [data ha — 2.2837 (done)
- p2: Adaptive alpha search for ComplementNB across epochs [optimization & s — 0.43512 (done)

**Verdict:** round best 0.43512 did not beat 0.43512

## Round 9 (2026-07-06T02:43Z)

- p0: Word-only TF-IDF + LogisticRegression with C search [model family] — 0.42797 (done)
- p1: SGDClassifier with elasticnet penalty + incremental training [regulari — 0.3908 (done)
- p2: Targeted goldmine stylometric features + current best CNB [feature rep — 0.40306 (done)

**Verdict:** round best 0.3908 did not beat 0.40306

## Round 10 (2026-07-06T03:03Z)

- p0: Mutual information feature selection + CNB on reduced feature set [reg — 0.40306 (done)
- p1: Bootstrap-aggregated (bagging) CNB ensemble [ensembling] — 0.39725 (done)
- p2: Dual-channel CNB: separate char and word models with averaged predicti — 0.44979 (done)

**Verdict:** KEPT p1 (Bootstrap-aggregated (bagging) CNB ensemble [ensembling]) — new best 0.39725

## Round 11 (2026-07-06T03:23Z)

- p0: LogisticRegression bagging ensemble [model family] — 0.39725 (done)
- p1: Stylometric feature augmentation for bagging CNB [feature representati — 0.43983 (done)
- p2: Word-only TF-IDF bagging CNB ensemble (remove character n-grams) [regu — 0.44917 (done)

**Verdict:** round best 0.39725 did not beat 0.39725

## Experiment 39 — reverted ❌ (candidate=n/a, best=0.397456)
- changed: model.py, train.py

(no summary written)
