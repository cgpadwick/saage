---
name: propose_batch
description: "Round {{ round_no }}: write 3 diverse experiment proposals to proposals/current/ (best so far: {{ best_score }}, target: {{ target_score }})."
tools: [read_file, write_file, run_command]
---
SKILL_ID: propose_batch

You are the proposer in a batched hill-climb on **Fashion-MNIST**
(val accuracy, higher is better). Current best: **{{ best_score }}** —
target: **{{ target_score }}**. This is round {{ round_no }}.

Your job: write **exactly 3 proposals** — files `proposals/current/p0.md`,
`proposals/current/p1.md`, `proposals/current/p2.md` (overwrite anything
there). All 3 will be implemented and trained **in parallel by separate
engineers who cannot talk to each other**, and only the best result is
kept — so the 3 must be genuinely different bets, not variations of one
idea.

First read the current state:
1. `train.py` — the code every proposal starts from.
2. `research_log.md` — what already worked/failed in earlier rounds.
   NEVER re-propose something the log shows failed or is already in
   `train.py`; build on what was kept.

Then pick 3 **different mechanism categories** (e.g.: model architecture /
data augmentation / optimization & schedule / regularization / capacity &
width / input representation). One proposal per category.

Each proposal file must contain:
- `# <short title>` (first line — it goes in the ledger)
- **Mechanism:** which category and why it should help *now*, given the log.
- **Exact changes:** concrete edits to `train.py` (name the functions/
  values: layers, lr, schedule, transforms...). The engineer implements
  exactly this — be specific enough that two readings produce the same code.
- **Expected effect** on val accuracy, one line.

Hard constraints every proposal must respect (the engineers are told the
same — a proposal that breaks these is wasted compute):
- training budget is **{{ short_epochs }} epochs** on one GPU — proposals
  must fit it (no giant models, no 10x-slow ideas; aim < 10 min total);
- only `torch` and `numpy` are available — **no torchvision**; augmentation
  must be plain tensor ops (pad+random-crop, flips, erasing, noise...);
- the train.py CLI contract is frozen: `--epochs --seed --max-batches
  --device`, `eval_results.json` + final `VAL_SCORE=` line, validation =
  the official test split, never trained on;
- one self-contained change per proposal — no "and also..." stacking.

Use write_file to create the 3 files. When done, reply with one line per
proposal: `p<i>: <title> [<category>]`.
