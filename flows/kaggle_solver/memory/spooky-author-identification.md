# spooky-author-identification — run kaggle_solver-20260703-1824-759b

outcome: medal=none above_median=true val=0.3332 test=0.3481 llm_cost=$5.6592

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

## Experiment 1 — KEPT ✅ (candidate=0.403476, best=0.403476)
- changed: competition_understanding.md, data_analysis.md, eda_char_ngrams.py, eda_overview.py, eda_text_lengths.py, eda_vocabulary.py, memory/spooky-author-identification.md, model.py, predict.py, tests/test_smoke.py, train.py
- commit: de84d4f1

(no summary written)
## Data audit (after baseline)
LEAKAGE: TF-IDF vectorizer fitted on ALL texts before train/val split — val set influences IDF weights used for training features (train.py:71 `clf.fit_vectorizer(texts)`). The `fit` must happen after `train_test_split`, on only the training texts.
UNUSED DATA: none — all provided data files (train.csv, test.csv) are loaded; sample_submission.csv is a format reference, not model input.
OPPORTUNITY: Fix the vectorizer leakage first, then consider adding sentence-level features (text length, punctuation density, vocabulary richness) as dense side-inputs to the NN — char n-grams alone miss syntactic stylometric signals that discriminate Poe/Shelley/Lovecraft.

## Experiment 2 — reverted ❌ (candidate=0.405376, best=0.403476)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py

Widen hidden layers from 256→128 to 512→256 units, add Batch Normalization after each linear layer (before ReLU), and remove dropout entirely (default 0.0). The ablation showed dropout removal improved log loss (0.4802 vs 0.4887 baseline), indicating underfitting is the bottleneck, so increasing capacity and stabilizing training with BN should lower validation loss further.

## Experiment 3 — reverted ❌ (candidate=1.03796, best=0.403476)
- changed: model.py, tests/test_smoke.py, train.py

Replace the 3-layer NN (256→128 hidden, ~13M params) with single-layer logistic regression (50K→3, ~150K params + L2 weight decay 0.01) and fix data leakage by fitting the TF-IDF vectorizer after the train/test split. This tests whether a linear model with proper regularization outperforms the overparameterized NN, since widening made validation loss worse and the literature gold standard for this dataset size is logistic regression on character n-gram TF-IDF.

## Experiment 4 — reverted ❌ (candidate=0.413801, best=0.403476)
- changed: model.py, predict.py, tests/test_smoke.py, train.py

Replace SimpleNN (char n-gram TF-IDF only) with a DualInputNN adding 26 stylometric features (punctuation, pronouns, dialect). Based on analysis showing strong author signals (MWS semicolons 2.4×, `i`/`my` 1.5–2.5×), the hypothesis is explicit stylometric pathways reduce the hypothesis space from sparse 50K n-grams alone, lowering validation log loss.

## Experiment 5 — reverted ❌ (candidate=0.422392, best=0.403476)
- changed: train.py

Fixes TF-IDF data leakage by moving vectorizer fit to after the train/validation split (so IDF weights come only from training texts) and adds L2 weight decay (1e-4) to Adam optimizer. This eliminates the contaminated validation metric that inflated the ~0.403 baseline and prevents overfitting on the now-smaller effective training set.

## Experiment 6 — reverted ❌ (candidate=0.500606, best=0.403476)
- changed: model.py, predict.py, tests/test_smoke.py, train.py

Fixes TF-IDF data leakage (vectorizer fit on train only, not all texts) and replaces the overparameterized 13M-param PyTorch SimpleNN with sklearn's LogisticRegression (multinomial, lbfgs solver, C=1.0). Hypothesis is that proper L2-regularized logistic regression with second-order convergence will outperform the badly conditioned NN and fix the experimental plateau.

## Experiment 7 — KEPT ✅ (candidate=0.371445, best=0.371445)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py, predict.py, requirements.txt, tests/test_smoke.py, train.py
- commit: 0eea9a64

Replace the char-n-gram TF-IDF + 2-layer NN pipeline with a fine-tuned DistilBERT-cased transformer (max_length=128, AdamW lr=2e-5, linear warmup+decay, EMA decay=0.999, temperature scaling) to capture sequential, contextual, and syntactic stylometric signals that bag-of-n-gram features miss. This directly tests the architecture_depth ablation target, fixes the TF-IDF contamination issue, and mirrors a prior successful run where the same upgrade dropped log loss from ~0.41 to ~0.36.

## Experiment 8 — KEPT ✅ (candidate=0.367506, best=0.367506)
- changed: model.py
- commit: 559a552c

Deepen DistilBERT classification head from a single linear layer to a 2-layer MLP (768→256→3, GELU, dropout 0.3) to capture non-linear stylometric interactions (pronouns, punctuation, function-word co-occurrences) that a linear probe cannot exploit, motivated by an ablation study where removing depth increased log loss by +0.09.

## Experiment 9 — KEPT ✅ (candidate=0.361996, best=0.361996)
- changed: model.py
- commit: 15679e40

Replaced fixed [CLS] token extraction with learned attention-weighted pooling over all token hidden states in DistilBertClassifier. This adds ~768 parameters (an attention vector) to let the model adaptively weigh the most diagnostic tokens for authorship (e.g., punctuation, function words, dialect markers) per sample, hypothesizing that the deeper representation pathway will improve validation log loss.

## Experiment 10 — reverted ❌ (candidate=0.366332, best=0.361996)
- changed: model.py

Deepened the DistilBERT classification head from 2 layers (768→256→3) to 3 layers with wider first hidden layer (768→512→256→3) and added Layer Normalization after each hidden layer. This tests whether extra depth and training stability from LayerNorm improve validation log loss, since the ablation study showed architecture depth was the largest driver of degradation and each prior depth increase has yielded steady gains (0.4035→0.3620).

## Experiment 11 — KEPT ✅ (candidate=0.358148, best=0.358148)
- changed: model.py, tests/test_smoke.py
- commit: 5c27bda9

Concatenates the [CLS] token (semantic/sentence-level) with the attention-pooled vector (stylometric/token-level), widening the DistilBert classifier head input from 768→1536 dimensions, while keeping the 2-layer MLP structure. Hypothesis: combining complementary representations (pretraining-optimized [CLS] + task-optimized pooling) will improve validation log loss, avoiding the deepening approach that failed in Exp 10.

## Experiment 12 — KEPT ✅ (candidate=0.356067, best=0.356067)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py
- commit: 5504515f

Replaced single-head AttentionPooling with 8-head MultiHeadAttentionPooling (768→6,144 attention params, output still 768 via averaging). The goal is to let separate heads specialize in different stylistic signals (punctuation, function words, character names) simultaneously, removing the trade-off forced by a single attention distribution, to improve validation log loss.

## Experiment 13 — reverted ❌ (candidate=0.359189, best=0.356067)
- changed: model.py

Added 8 learnable per-head temperature scalars (initialized to 1.0) to the MultiHeadAttentionPooling softmax, so each attention head can independently control how sharp or diffuse its attention distribution is. The hypothesis is that different stylistic signals need different sharpness — rare character names like "Raymond" benefit from low temperature (sharp focus on a few tokens), while broad function-word patt
… (truncated)
