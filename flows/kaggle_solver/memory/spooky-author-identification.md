# spooky-author-identification — run kaggle_solver-20260704-0941-1ee2

outcome: medal=none above_median=true val=0.3524 test=0.3638 llm_cost=$1.8485

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

## Experiment 1 — KEPT ✅ (candidate=0.51919, best=0.51919)
- changed: competition_understanding.md, eda_entities.py, eda_function_words.png, eda_function_words.py, eda_ngrams.py, eda_overview.py, eda_text_analysis.py, eda_text_length.png, eda_text_lengths.py, memory/nomad2018-predict-transparent-conductors.md, memory/spooky-author-identification.md, model.py, predict.py, test.csv, tests/test_smoke.py, train.csv, train.py
- commit: ff8f13bb

(no summary written)
## Data audit (after baseline)

LEAKAGE: none found
- TF-IDF vectorizer is fit on `X_train` (the training split only) after `train_test_split` — clean.
- No target encoding, no feature engineering from labels, no test data influence at training time.
- Validation split is disjoint (stratified 80/20 holdout).

UNUSED DATA: all data used
- `data/train.csv` (text + author) and `data/test.csv` (text) are both loaded. `sample_submission.csv` and `description.md` are non-signal metadata.

OPPORTUNITY:
- The baseline uses **only character n-gram TF-IDF**. Adding word n-grams, syntactic features (POS tags, punctuation counts), sentence-length statistics, and vocabulary richness could unlock substantial signal from the same text field.
- No external corpora (full Poe/Lovecraft/Shelley texts) are leveraged — these are publicly available and could supplement the relatively small training set (~17k sentences).

## Experiment 2 — reverted ❌ (candidate=0.618044, best=0.51919)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py

Swap LogisticRegression regularization from pure L2 (lbfgs) to elastic net L1+L2 (saga solver, l1_ratio=0.5) to test whether L1 sparsity improves validation log loss by selecting only discriminative character n-grams while retaining L2 stability. All other code (TF-IDF vectorizer, pipeline, training args) stays identical.

## Experiment 3 — reverted ❌ (candidate=0.790573, best=0.51919)
- changed: train.py

Decreased LogisticRegression C from 1.0 to 0.1 (10× stronger L2 regularization) by changing the default --lr from 1.0 to 10.0. Since the 50k-dimensional char n-gram model is highly regularization-dependent (ablation without regularization exploded log loss from 0.70 to 2.14), stronger shrinkage should curb overfitting to rare n-grams and improve validation log loss while preserving the proven L2+lbfgs combination.

## Experiment 4 — KEPT ✅ (candidate=0.453225, best=0.453225)
- changed: model.py, tests/test_smoke.py, train.py
- commit: 5f3f44f7

Replace the single char-n-gram TF-IDF (50k) with a FeatureUnion of char TF-IDF (35k, ngram_range 2-7) plus word uni+bigram TF-IDF (15k, ngram_range 1-2) into LogisticRegression (C=1.0). The hypothesis is that word n-grams capture complementary topical signal — the ablation study showed removing them degraded loss by +0.0459 — so adding them should improve validation log loss over the char-only model.

## Experiment 5 — KEPT ✅ (candidate=0.450648, best=0.450648)
- changed: model.py
- commit: 942e7d62

Added a StylometricExtractor as a third FeatureUnion branch alongside char TF-IDF (35k) and word TF-IDF (15k), producing ~70 handcrafted features: punctuation densities, function-word ratios, sentence/word length stats, uppercase ratio, and vocabulary richness. The hypothesis is that these direct stylometric signals (function-word profiles and punctuation habits) are more discriminative for authorship than TF-IDF captures indirectly, especially for short texts.

## Experiment 6 — KEPT ✅ (candidate=0.438104, best=0.438104)
- changed: model.py
- commit: 2d4e56f9

Switches the char TF-IDF vectorizer's analyzer from `char` to `char_wb` (word-boundary-aware n-grams) to give the classifier cleaner features for function words and punctuation — the strongest authorship signals. A single-line code change, with no other modifications to the pipeline.

## Experiment 7 — reverted ❌ (candidate=0.439465, best=0.438104)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py

Increase the char_wb TfidfVectorizer ngram_range upper bound from 7 to 10 (keeping max_features=35000 fixed) to let the model capture full long words (13% of tokens are >7 chars) that are author-discriminative, like Lovecraft's "eldritch" or Poe's "nevermore", which the current limit only captures as fragmented substrings. The richer candidate pool should improve validation log loss and follows the phase's focus on the highest-leverage char TF-IDF component.

## Experiment 8 — KEPT ✅ (candidate=0.437663, best=0.437663)
- changed: model.py
- commit: 0717fe4e

Adds `max_df=0.85` to the char_wb TfidfVectorizer (previously default 1.0, keeping all n-grams). This prunes ultra-common character substrings (e.g., "the", "and", "ing") that appear in >85% of documents and carry negligible authorship signal, freeing feature slots for more discriminative mid-frequency patterns. Based on literature recommending moderate document-frequency filtering for character n-gram authorship attribution.

## Experiment 9 — reverted ❌ (candidate=0.44099, best=0.437663)
- changed: model.py

Switches the char_wb TfidfVectorizer from frequency-based to binary (presence/absence) encoding via `binary=True`, based on the hypothesis that authorship attribution relies more on which character n-grams an author uses (stylistic fingerprint) than on their exact frequency, reducing topic noise and text-length confounding.

## Experiment 10 — KEPT ✅ (candidate=0.435932, best=0.435932)
- changed: model.py
- commit: 3b276544

Disables lowercase normalization in the char_wb TfidfVectorizer (`lowercase=True`→`False`) so capitalized author-significant names (e.g., Cthulhu, Ligeia, Frankenstein) remain distinct from their lowercase forms, preserving a capitalization signal that prior experiments erased. Hypothesis: this boosts validation log loss because author-specific capitalization patterns are discriminative.

## Experiment 11 — reverted ❌ (candidate=0.436177, best=0.435932)
- changed: model.py

Changed TfidfVectorizer sublinear_tf True→False for char_wb features in model.py. With lowercase=False (kept) creating rare capitalized n-grams and max_df=0.85 (kept) already pruning ubiquitous features, linear TF preserves discriminative frequency ratios that sublinear scaling would compress and dilute for authorship attribution.

## Experiment 12 — reverted ❌ (candidate=0.437449, best=0.435932)
- changed: ablation_history.md, ablation_study.py, ablation_summary.md, model.py

Changes TfidfVectorizer ngram_range (1,2)→(1,3) to add word trigrams, capturing author-specific three-word collocations (e.g., "the tell-tale" for Poe) that bigrams miss. This is the first refinement of the word n-gram pipeline since Experiment 4, intended to improve validation log loss by diversifying signal beyond the plateaued char_wb features.

## Experiment 13 — KEPT ✅ (candidate=0.435504, best=0.435504)
- changed: model.py
- commit: 9669666e

Sets word TF-IDF sublinear_tf to False (was True) to preserve linear function-word frequency ratios — the strongest authorship signal per Mosteller & Wallace (1964) — rather than compressing them with log scaling. Char_wb retains sublinear_tf=True since its character patterns have a different signal profile. Single-parameter change on the word branch only.

## Experiment 14 — KEPT ✅ (candidate=0.4
… (truncated)
