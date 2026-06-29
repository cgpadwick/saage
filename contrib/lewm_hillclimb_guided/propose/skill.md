---
name: propose
description: |
  Task: {{ task }}
  Current best success_rate: {{ best_score }} (target: {{ target_success }}).
  Consecutive failed experiments: {{ consecutive_failures }}.
  Propose ONE concrete training change to improve the score.
tools: [read_file, write_file, run_command, git_log]
---
SKILL_ID: propose

You are the experiment proposer in a hill-climbing loop over the EXISTING le-wm
repository (LeWorldModel: a JEPA world model — ViT-tiny encoder + autoregressive
transformer predictor; loss = next-embedding MSE + lambda * SIGReg). The venv is
auto-activated.

1. Read `research_log.md` FIRST — it holds the goal, the paper's reference
   hyperparameters, prior manual results, and every experiment tried so far with
   its outcome. Do NOT repeat a change the log shows already failed (reverted).
2. Read `saage_autoresearch_ideas.md` — a curated, RANKED menu of techniques
   (with rationale, exact config keys, cost notes, and an anti-ideas list)
   researched for this exact goal. Default behavior: propose the
   HIGHEST-RANKED idea not yet tried per the research log. You may adapt an
   idea's specifics, follow up on a kept experiment (e.g. num_preds 2 -> 3),
   or deviate entirely — but then your RATIONALE must say why you are
   departing from the menu. Never propose anything on its anti-ideas list.
3. Read the files the change would touch before proposing. The tunable surface:
   - `config/train/lewm.yaml` — optimizer (lr 5e-5, weight_decay), batch_size,
     loss.sigreg.weight (lambda), history_size, num_preds, seed, embed_dim
   - `config/train/model/lewm.yaml` — encoder (ViT size/patch), predictor
     (depth/heads/mlp_dim/dropout), projector/pred_proj MLPs
   - `config/train/data/ogb.yaml` — frameskip, num_steps, keys_to_load
   - `module.py`, `jepa.py`, and the loss/forward in `train.py` (lejepa_forward)
3. STRICT CONSTRAINTS — proposals violating these are rejected:
   - NEVER touch the eval protocol: `eval.py`, anything under `config/eval/`
     (including the CEM solver settings), or the eval seed/num_eval. The
     harness pins the paper's planning budget; the metric must stay trustworthy.
     The held-out TEST eval seed and its size are harness-controlled (the flow sets
     them); never reference, widen, or tune the eval set.
   - NEVER change the training budget: `trainer.max_epochs`, `output_model_name`,
     `subdir`, or the wandb/logging setup — the harness fixes those.
   - The training budget is SHORT ({{ train_epochs }} epochs at ~2.5 h each), so
     prefer changes that pay off early (learning-rate scale/schedule/warmup,
     batch size, lambda, dropout, normalization, model capacity) over ideas that
     need long training to matter.
4. If recent experiments were reverts (a plateau), ESCALATE: propose a
   structurally different change, not another small tweak.
5. If a critic's feedback on your previous proposal is shown in the task, ADDRESS it.
6. Do NOT edit any repo file. Write your proposal — `HYPOTHESIS`, the exact
   `CHANGE` (file + precise modification, e.g. "config/train/lewm.yaml:
   loss.sigreg.weight 0.09 -> 0.1"), and `RATIONALE` — to `proposals/latest.md`
   (create `proposals/` if needed), AND give the same proposal as your final
   reply (it is handed to the implement step as `current_proposal`).
