# kaggle_solver benchmark results

Autonomous runs of `flows/kaggle_solver` graded with `mlebench grade`.
Regenerate with `python flows/kaggle_solver/bench.py table` — the
source of truth is `benchmark_journal.jsonl` (one line per run).

| date | competition | model | medal | above median | val score | test score | LLM cost | GPU hours | run |
|---|---|---|---|---|---|---|---|---|---|
| 2026-07-02 | spooky-author-identification | openrouter | unknown | ? | 0.3207 | ? | $4.9468 | 10.2 | `kaggle_solver-20260702-0425-fdc6` |
| 2026-07-03 | spooky-author-identification | openrouter | none | true | 0.3985 | 0.4129 | $2.2424 | 2.4 | `kaggle_solver-20260703-1603-dc9a` |
| 2026-07-03 | nomad2018-predict-transparent-conductors | openrouter | none | false | 0.05178 | 0.1424 | $7.2623 | 5.7 | `kaggle_solver-20260703-1603-d093` |
| 2026-07-03 | spooky-author-identification | openrouter | none | true | 0.3332 | 0.3481 | $5.6592 | 10.6 | `kaggle_solver-20260703-1824-759b` |
| 2026-07-04 | nomad2018-predict-transparent-conductors | openrouter | none | false | 0.05181 | 0.5154 | $19.7740 | 6.7 | `kaggle_solver-20260704-0617-b546` |
| 2026-07-04 | spooky-author-identification | openrouter | none | true | 0.3524 | 0.3638 | $1.8485 | 11.2 | `kaggle_solver-20260704-0941-1ee2` |
| 2026-07-04 | spooky-author-identification | openrouter | none | true | 0.3496 | 0.3581 | $2.0475 | 10.1 | `kaggle_solver-20260704-2106-ec27` |
