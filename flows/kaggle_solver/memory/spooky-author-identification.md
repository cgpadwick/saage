# spooky-author-identification — run kaggle_solver-20260703-1603-dc9a

outcome: medal=none above_median=true val=0.3985 test=0.4129 llm_cost=$2.2424

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

## Experiment 1 — KEPT ✅ (candidate=0.408412, best=0.408412)
- changed: competition_understanding.md, data_analysis.md, eda_features.py, eda_function_words.py, eda_overview.py, eda_text_analysis.py, model.py, predict.py, tests/test_smoke.py, train.py
- commit: db2a5bbe

(no summary written)

## Experiment 2 — KEPT ✅ (candidate=0.408148, best=0.408148)
- changed: model.py, predict.py, tests/test_smoke.py, train.py
- commit: 845a7c3a

Character n-gram TF-IDF features (n=2–5, 20k features) will be concatenated with the existing word n-gram features (20k→40k total) to capture spelling variants, punctuation patterns, and subword morphology — closing the 10.7% OOV test vocabulary gap that the word-only model cannot bridge.

## Experiment 3 — reverted ❌ (candidate=0.450628, best=0.408148)
- changed: model.py, predict.py, tests/test_smoke.py, train.py

Replace the PyTorch MLP (20.6M params) with sklearn's LogisticRegression (multinomial, L2-regularized, ~120k params) to reduce severe overfitting on ~14k training samples with 40k TF-IDF features. Linear models with L2 penalty are the canonical strong baseline for high-dimensional sparse text, converging deterministically and generalizing far better than the overparameterized MLP.

## Experiment 4 — KEPT ✅ (candidate=0.398496, best=0.398496)
- changed: model.py, train.py
- commit: 389226f3

Reduced MLP hidden_dim 512→128, raised dropout 0.3→0.4, added BatchNorm1d after each linear layer, and added weight_decay=1e-4 to the Adam optimizer. The hypothesis is that the 20.6M-parameter network is severely overparameterized for ~14k training samples, and stronger regularization will force the model to learn generalizable stylistic patterns instead of memorizing.

## Experiment 5 — reverted ❌ (candidate=0.426338, best=0.398496)
- changed: model.py, tests/test_smoke.py

The proposal adds ~20 hand-crafted stylistic features (punctuation ratios, pronoun densities, dialect flags, sentence length) as dense columns alongside the existing 40k TF-IDF char n-grams. These give the model explicit, position-invariant signals—like semicolon rate and first-person pronoun density—that are currently diluted across sparse n-gram dimensions and poorly captured by the overparameterized representation.

## Experiment 6 — reverted ❌ (candidate=0.426222, best=0.398496)
- changed: train.py

Changed `hidden_dim` in train.py from 512→128 to match the model class default, cutting parameters from ~20.6M to ~5.1M. The aim is to reduce overfitting on ~14k training samples while retaining the BatchNorm, dropout (0.4), and weight_decay that improved validation logloss in Experiment 4 from 0.408→0.398.

## Experiment 7 — reverted ❌ (candidate=0.419581, best=0.398496)
- changed: train.py

Adds label smoothing (ε=0.1) to CrossEntropyLoss in train.py (nn.CrossEntropyLoss() → nn.CrossEntropyLoss(label_smoothing=0.1)). This directly targets the logloss metric by calibrating predicted probabilities to prevent the catastrophic penalty (~34.5 per mistake) from overconfident wrong predictions, especially on strong named-entity features like author names.

## Experiment 8 — reverted ❌ (candidate=0.400441, best=0.398496)
- changed: train.py

The current experiment replaces the Adam optimizer (fixed LR=0.001) with AdamW (decoupled weight decay) and adds a CosineAnnealingLR scheduler, decaying learning rate from 0.001 to near-zero over the epoch budget, to help the optimizer escape sharp minima and converge to flatter, better-generalizing basins, directly improving validation logloss.

## Experiment 9 — reverted ❌ (candidate=0.408172, best=0.398496)
- changed: model.py, predict.py, tests/test_smoke.py, train.py

Switching from TF-IDF n-grams + MLP to a DistilBERT sequence classifier fine-tuned end-to-end (AdamW lr=2e-5, max_length=128, first 2 of 6 layers frozen, batch_size=32) to push validation logloss below 0.398. DistilBERT captures word order, clause structure, and syntactic context, and its WordPiece tokenization solves the 10.7% test OOV gap—bag-of-ngrams has plateaued because it discards word order entirely.

## Experiment 10 — reverted ❌ (candidate=0.409491, best=0.398496)
- changed: train.py

Reducing the MLP hidden dimension from 512 to 256 in the TF-IDF+MLP pipeline, halving parameters from ~20.6M to ~10.3M, to test whether 256 is the capacity sweet spot that improves generalization. The 512-dim model appears overparameterized (~1,470 params/sample) while the earlier 128-dim trial (0.426 logloss) lacked sufficient capacity — 256 remains unexplored.

## Experiment 11 — reverted ❌ (candidate=0.91506, best=0.398496)
- changed: model_xgb.py, predict.py, tests/test_smoke.py, train.py

Replaced the PyTorch 2-layer MLP with an XGBoost classifier (800 trees, max_depth=6, colsample_bytree=0.8, gamma=0.1) on the same 40k-dimensional word+char TF-IDF features, hypothesizing that tree ensembles handle high-dimensional sparse data and non-linear feature interactions better than the plateaued MLP (0.3985 logloss). XGBoost directly optimizes multi-class logloss, natively uses sparse matrices, and adds pruning-based regularization; if it improves, the next step is an MLP+XGBoost ensemble.

## Experiment 12 — reverted ❌ (candidate=0.417689, best=0.398496)
- changed: train.py

Adds MixUp data augmentation (α=0.2) to the MLP training loop, generating convex combinations of TF-IDF features and soft targets during training. This is a fundamentally new form of regularization that creates synthetic training data to produce smoother decision boundaries and better-calibrated probabilities — directly targeting the overconfident wrong predictions that dominate the logloss metric.

## Experiment 13 — reverted ❌ (candidate=0.408646, best=0.398496)
- changed: train.py

Experiment 13 adds Exponential Moving Average (EMA, decay=0.999) of model parameters during training, evaluating the EMA weights at the end and saving them if they beat the best checkpoint. The hypothesis is that EMA smooths batch-level oscillations and biases toward flatter minima, directly reducing generalization error — a temporal-averaging fix orthogonal to previous architecture/feature changes, targeting the existing best logloss of 0.40044 to push below 0.3985.

## Experiment 14 — reverted ❌ (candidate=0.424415, best=0.398496)
- changed: train.py

Replaces standard CrossEntropyLoss with Focal Loss (γ=2.0) to reshape the loss objective toward hard, ambiguous examples and penalize overconfident predictions, aiming to improve validation logloss after nine consecutive experiments failed to beat 0.3985.
