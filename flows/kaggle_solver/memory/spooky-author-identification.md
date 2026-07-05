# spooky-author-identification — run kaggle_solver-20260704-2106-ec27

outcome: medal=none above_median=true val=0.3496 test=0.3581 llm_cost=$2.0475

## Research log

# spooky-author-identification — kaggle solver research log

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

## Experiment 1 — KEPT ✅ (candidate=9.35641, best=9.35641)
- changed: competition_understanding.md, data_analysis.md, eda_chars.py, eda_features.py, eda_function_words.png, eda_ngrams.py, eda_overview.py, eda_punctuation.png, eda_stats.csv, eda_text_length.png, eda_word_count.png, memory/nomad2018-predict-transparent-conductors.md, memory/spooky-author-identification.md, model.py, predict.py, tests/test_smoke.py, train.py
- commit: add57205

(no summary written)

## Experiment 2 — KEPT ✅ (candidate=0.86666, best=0.86666)
- changed: model.py, predict.py, train.py
- commit: 7a81cf35

(no summary written)
## Data audit (after baseline)
LEAKAGE: none found
UNUSED DATA: all provided files used (train.csv, test.csv, sample_submission.csv, description.md). No extra modalities ignored.
OPPORTUNITY: 
- Baseline uses char n-grams only; word-level n-grams or TF-IDF unigrams+bigrams capture topical/semantic signal that chars miss.
- No pre-trained embeddings (GloVe/fastText/LLM) are used — a 300d GloVe mean-pooling feature would add cheap semantic signal orthogonal to char-SVD.

## Experiment 3 — reverted ❌ (candidate=n/a, best=0.86666)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, train.py

Aggressively increase LGBM tree capacity for the 15-round budget: learning_rate 0.05→0.5, num_leaves 63→255, min_child_samples 20→5, reg_alpha/reg_lambda 0.01→0.0, and subsample/colsample_bytree 0.8→1.0. The hypothesis is that the model is severely underfitting at 15 rounds (val log-loss 0.8666 barely beats 0.911 baseline), and per-round capacity must be maximized since overfitting is impossible with so few rounds.

## Experiment 4 — KEPT ✅ (candidate=0.562331, best=0.562331)
- changed: model.py, predict.py, train.py
- commit: a3126761

Replace LightGBM (gradient boosting) with multinomial LogisticRegression (L2, L-BFGS) in the classifier pipeline, keeping the same char-SVD + stylometric features. The hypothesis is that logistic regression converges in one pass, whereas boosting cannot build a strong ensemble in 15 rounds, so switching classifiers should improve log-loss from 0.8666 toward the ~0.35 level seen in a prior LR run.

## Experiment 5 — reverted ❌ (candidate=n/a, best=0.562331)
- changed: model.py

Replaced LogisticRegression with RandomForestClassifier (500 trees, min_samples_leaf=5, balanced_subsample) and removed StandardScaler since tree models are scale-invariant. The goal is to capture non-linear feature interactions (e.g., short sentence + high punctuation density → Poe) that the linear decision boundary misses, improving validation log-loss from 0.56233 to below 0.50.

## Experiment 6 — reverted ❌ (candidate=0.606389, best=0.562331)
- changed: model.py

Replace LogisticRegression with MLPClassifier (single 100-neuron hidden layer, ReLU, Adam, early stopping) to capture non-linear feature interactions that linear LR misses, while directly optimizing log-loss (cross-entropy) for better calibrated probability estimates than RandomForest.

## Experiment 7 — KEPT ✅ (candidate=0.480233, best=0.480233)
- changed: model.py, train.py
- commit: 16e0c5ac

Remove TruncatedSVD(300) from the char TF-IDF pipeline so that the full 50k-dimensional sparse n-gram matrix feeds directly into LogisticRegression, plus increase max_iter to 1000 for convergence. The hypothesis is that SVD discards rare but highly discriminative character patterns (e.g., "'ymon'", "'n't'", "'....'") that are the strongest authorship signals, and restoring them should improve validation log-loss from 0.56233 toward ~0.40–0.45.

## Experiment 8 — KEPT ✅ (candidate=0.427741, best=0.427741)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py
- commit: 867a47ee

Weaken L2 regularization by changing LogisticRegression C from 1.0 to 3.0, so the model can assign non-trivial weight to mid-frequency author-specific char n-gram patterns (e.g., named entities, dialect markers) that were over-regularized before. A diagnostic sweep on the exact pipeline shows C=3.0 achieves val_loss ≈0.428 vs C=1.0's 0.479, an ~11% improvement, while keeping the coefficient norm (≈98) well below the catastrophic regime seen at C=1e10.

## Experiment 9 — reverted ❌ (candidate=0.430378, best=0.427741)
- changed: model.py

Added `class_weight='balanced'` to LogisticRegression (keeping C=3.0) to force the model to pay equal attention to each author class via inverse-frequency loss reweighting, countering moderate class imbalance (EAP 40% vs HPL 29%) that biases the unweighted L2 model toward overconfident majority-class predictions and hurts calibration log-loss.

## Experiment 10 — reverted ❌ (candidate=n/a, best=0.427741)
- changed: model.py, train.py

Replaces the logistic regression solver from lbfgs (L2 only) to saga with ElasticNet penalty (l1_ratio=0.15, C=3.0 unchanged) so that irrelevant rare char n-gram weights are driven to exactly zero while preserving L2 stability, testing whether this built-in feature selection reduces overfitting on the 50k-dimensional sparse feature space and improves validation log-loss beyond the C=3.0 baseline.

## Experiment 11 — KEPT ✅ (candidate=0.427423, best=0.427423)
- changed: model.py
- commit: 7c289bcb

Adds min_df=3 to the char TfidfVectorizer (ngram_range (2,7), max_features=50k) to prune n-grams appearing in ≤2 training documents. This directly regularizes the feature extraction stage—complementing prior classifier-side regularization (C=3.0)—by dropping rare noise patterns that cause memorization, forcing the model to rely on more generalizable char subword signals.

## Experiment 12 — reverted ❌ (candidate=n/a, best=0.427423)
- changed: model.py

Switched LogisticRegression from L2 penalty (solver='lbfgs') to pure L1/Lasso (solver='saga', penalty='l1') while keeping C=3.0 and all other pipeline components unchanged, hypothesizing that L1's feature selection will zero out most of the 50k noisy char n-gram features and focus capacity on the rare, highly discriminative subword patterns (e.g., 'ymon', "an'", '....') to break through the current 0.4274 log-loss plateau.

## Experiment 13 — KEPT ✅ (candidate=0.39342, best=0.39342)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py
- commit: 13cfe60f

Adds word n-gram TF-IDF (ngram_range=(1,2), max_features=15000) as a third FeatureUnion branch alongside char TF-IDF and stylometric features. Hypothesis: function-word ratios (the, upon, her, etc.) are the strongest discriminators and are captured cleanly at word level, whereas char n-grams only approximate them via noisy substrings.

## Experiment 14 — reverted ❌ (candidate=0.39342, best=0.39342)
- changed: model.py

Changed word TF-IDF max_df from 0.85 to 1.0 to restore highly discriminative function words (e.g., "upon", "her", "she") that appear in >85% of documents and were being silently dropped. The hypothesis is that these features provide orthogonal signal that char n-grams cannot cleanly capture, making the word branch non-redundant and improving author discrimination.

## Experiment 15 — KEPT ✅ (candidate=0.387757, best=0.387757)
- changed: model.py
- commit: 6fed0d43

Weaken LogisticRegression L2 regularization by increasing C from 3.0 to 10.0, testing whether a 3.3× reduction in regularization lets the model assign stronger w
… (truncated)
