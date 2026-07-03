# spooky-author-identification — run kaggle_solver-20260702-0425-fdc6

outcome: medal=unknown above_median=? val=0.320690393447876 test=? llm_cost=$4.9468

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

## Experiment 1 — KEPT ✅ (candidate=0.411012, best=0.411012)
- changed: competition_understanding.md, data_analysis.md, eda_deep.py, eda_fw_by_author.png, eda_fw_correlation.png, eda_overview.py, eda_punctuation_by_author.png, eda_target_distribution.png, eda_text_length_by_author.png, eda_textlen_boxplot.png, eda_word_count_by_author.png, eda_wordcount_boxplot.png, model.py, predict.py, tests/test_smoke.py, train.py
- commit: 64c41518

(no summary written)

## Experiment 2 — KEPT ✅ (candidate=0.374785, best=0.374785)
- changed: model.py, predict.py, tests/test_smoke.py, train.py
- commit: 3fb190a2

Replaces the TF-IDF bigram vectorizer + small feedforward network with a fine-tuned DistilBERT classifier (distilbert-base-uncased + dropout + 3-class linear head). The hypothesis is that capturing contextual, semantic, and word-order signals will reduce validation log loss from ~0.411 to well below 0.20; sentences average only 27 words, fitting easily within the 128-token limit, and DistilBERT was the proven approach in the original competition.

## Experiment 3 — KEPT ✅ (candidate=0.360251, best=0.360251)
- changed: model.py, train.py
- commit: 8d5b568e

Switched the pretrained model from distilbert-base-uncased to distilbert-base-cased, keeping all other hyperparameters identical, because capitalization patterns (proper nouns, named entities, dialect representations, and formal address) are diagnostic for authorship attribution and the uncased tokenizer strips this signal.

## Experiment 4 — reverted ❌ (candidate=0.363268, best=0.360251)
- changed: model.py

Added label smoothing (ε=0.1) to CrossEntropyLoss in the DistilBERT-cased fine-tuning pipeline. The hypothesis is that soft targets will reduce overconfident predictions and improve probability calibration, directly lowering validation log loss (currently 0.360) with no added compute cost.

## Experiment 5 — reverted ❌ (candidate=0.365657, best=0.360251)
- changed: model.py, predict.py, train.py

Concatenate 21 hand-crafted stylometric features (punctuation rates, function-word frequencies, sentence-length statistics, and vocabulary richness) with the DistilBERT [CLS] embedding before the classification head, because authorship attribution is fundamentally a stylometric task and the pure-Transformer baseline has plateaued at 0.360 log loss with no improvement from label smoothing.

## Experiment 6 — reverted ❌ (candidate=0.372803, best=0.360251)
- changed: model.py, predict.py, tests/test_smoke.py, train.py

Replaced DistilBERT-cased (66M params) with BERT-base-cased (110M params), changing model class, tokenizer, imports, and reducing batch size from 16 to 8 to manage GPU memory. The hypothesis is that the larger model's additional capacity will better capture the subtle stylistic, punctuation, and named-entity patterns in these short 27-word sentences, pushing past DistilBERT's apparent saturation at 0.36025 validation log loss.

## Experiment 7 — reverted ❌ (candidate=0.379039, best=0.360251)
- changed: model.py

Replaces [CLS]-token pooling with mean-pooling (averaging all token embeddings from DistilBERT's last hidden layer). The hypothesis is that authorship signals (function words, punctuation, named entities) are distributed across these short texts, and mean pooling aggregates all positions rather than relying on a single [CLS] token — capturing more diagnostic signal at zero additional parameter cost.

## Experiment 8 — reverted ❌ (candidate=0.520268, best=0.360251)
- changed: model.py, predict.py, tests/test_smoke.py, train.py

Replaces DistilBERT fine-tuning with character 2–5-gram TF-IDF + Logistic Regression (C=1.0, class-weighted, L2 penalty). This swap is motivated by four consecutive DistilBERT attempts that failed to improve on the 0.360 validation log loss, and by the data analysis showing that character n-grams are the gold-standard representation for authorship attribution, capturing orthographic habits that BPE subword tokenization fragments.

## Experiment 9 — reverted ❌ (candidate=0.375981, best=0.360251)
- changed: model.py

Replaces single-final-layer [CLS] pooling in DistilBERT with a learned weighted sum of all 6 hidden layers' [CLS] vectors (6 extra parameters). Hypothesis: lower and middle layers capture surface-level and syntactic authorship signals (punctuation, function words) that the final semantic layer discards, reducing log loss from 0.360 to ~0.34.

## Experiment 10 — reverted ❌ (candidate=0.416893, best=0.360251)
- changed: train.py

Switches from a uniform learning rate (2e-5) to discriminative learning rates for DistilBERT fine-tuning: backbone LR drops to 5e-6 to prevent catastrophic forgetting of pretrained language patterns, while classification head LR rises to 1e-4 for faster convergence from random initialization. Six prior architecture experiments all failed, suggesting the bottleneck is optimization rather than representation.

## Experiment 11 — reverted ❌ (candidate=0.36146, best=0.360251)
- changed: model.py, predict.py, tests/test_smoke.py, train.py

Replaced DistilBERT-cased (66M params, 30k vocab) with RoBERTa-base (125M params, 50k vocab) in model.py, train.py, and predict.py, reducing batch size 16→8 to fit GPU memory. RoBERTa's larger byte-level BPE vocabulary preserves rare diagnostic authorial words (e.g., "eldritch", "nevermore") as single tokens, while its 10× larger pretraining corpus with dynamic masking yields more robust representations — aiming to break through DistilBERT's saturated ~0.360 log loss ceiling after seven consecutive failed modifications.

## Experiment 12 — KEPT ✅ (candidate=0.354505, best=0.354505)
- changed: model.py, train.py
- commit: d2270268

Add EMA weight-averaging (decay=0.999) initialized after epoch 1, with validation on EMA weights for epochs 2+. The goal is to smooth the noisy SGD trajectory toward flatter minima, producing better-calibrated probabilities and reducing validation log loss — targeting a break below 0.360 after eight prior experiments that all changed representations or loss functions rather than training dynamics.

## Experiment 13 — reverted ❌ (candidate=0.395862, best=0.354505)
- changed: model.py

Adding gradient norm clipping (max_norm=1.0) to the optimizer step in both AMP and non-AMP training paths, motivated by the hypothesis that clipping will stabilize DistilBERT fine-tuning by preventing destructive gradient steps and improving validation log loss beyond 0.3545. This zero-parameter-cost change complements the EMA smoothing from Experiment 12, addressing the identified training-dynamics bottleneck after eight failed architecture and feature experiments.

## Experiment 14 — KEPT ✅ (candidate=0.346272, best=0.346272)
- changed: model.py, predict.py, tests/test_smoke.py, train.py
- commit: b2efbc0f

After EMA training, learn a single temperature parameter T on the validation set to scale logits and soften overconfident DistilBERT probabilities, moving log loss from 0.3545 toward ~0.34. This directly targets calibration error — the dominant remaining loss source — and cannot hurt since T=1.0 leaves predictions unchanged.

## Experiment 15 — KEPT ✅ (candidate=0.340584, best=0.340584)
- changed: train.py
- commit: 0230fc9e

Replaced the linear LR scheduler with `get_cosine_schedule_with_warmup` in `train.py` (same 
… (truncated)
