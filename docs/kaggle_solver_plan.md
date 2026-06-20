# Kaggle Solver: competitive analysis + saage integration plan

**Date:** 2026-06-11 · **Status:** draft for discussion
**Goal:** make the kaggle solver (`src/mle_beast/benchmark/`) competitive with
current MLE-bench agents, and ship it as saage's flagship flow
(`pip install "saage[kaggle-solver]"`).

## 1. Where the field is (verified 2026-06)

| Agent | Org | Headline result | Load-bearing techniques |
|---|---|---|---|
| AIDE | Weco | 16.9% any-medal, full bench (o1-preview) — the original baseline scaffold | tree search over solution drafts |
| R&D-Agent | Microsoft | 22.2% full bench (GPT-5, official leaderboard — frozen 2026-04) | researcher/developer agent split |
| **MLE-STAR** | Google (in `google/adk-samples`) | **63–64% medal on Lite** (Gemini-2.5-Pro) | ① web-search-retrieved model recipes seed the initial solution ② **ablation studies** rank pipeline blocks → targeted block-level refinement ③ agent-proposed **ensembling** of parallel candidates ④ debugger + **data-leakage checker** + data-usage checker |
| ML-Master / 2.0 | SJTU | 29.3% full → **56.4% avg, 75.8% on Lite-difficulty** (2.0, 2026) | balanced **multi-trajectory exploration** (parallel tree) + selectively-scoped memory feeding reasoning; 2.0 adds hierarchical "cognitive accumulation" across tasks |
| DS-STAR | Google/KAIST | DABStep 45.2% (analytics, not Kaggle) | heterogeneous data-file analyzer; verifier+router iterative planning |

Reading of the gap-to-SOTA, in impact order: **(1) retrieval-grounded
solutions, (2) ablation-targeted refinement, (3) parallel candidates +
ensembling, (4) leakage/usage checkers, (5) cross-competition memory.**

## 2. Where our solver stands

Strengths (real, keep them): resilience engineering nobody's paper talks
about (critics complete-with-failure, abandon path, 429-storm survival),
ShortTrain/FinalTrain budget split, submission-contract validation, honest
`mlebench grade` integration, git keep/revert auditability, LLM score
extractor for arbitrary metrics. Evidence: **1 silver** (nomad2018, nemotron,
$0-class model) — but **no medal-rate measurement across Lite**, which is the
number everyone brags in.

Gaps vs the table: single greedy trajectory (no tree/parallelism), proposals
from LLM priors only (no web retrieval), no attribution of *which* pipeline
block matters (proposals are unfocused), winner-take-all (no ensembling), no
leakage/usage checkers, every competition starts cold.

## 3. Competitive plan (priority order)

- **P0 — Measurement harness before features.** Run the full Lite suite via
  `saage remote` (one competition per cheap GPU box — Thunder a6000 $0.47/hr /
  Lambda A10), grade, and publish a medal-rate table per model. Without this
  there is nothing to brag about and no way to know if P1–P5 help.
  *This is also the saage demo story itself: N parallel cloud handoffs from
  one YAML flow.*
- **P1 — Retrieval-grounded proposals** (MLE-STAR's biggest single win): a
  `web_search` tool for the CompUnderstanding and Proposal actors; seed the
  baseline from retrieved recent-model recipes instead of LLM priors.
- **P2 — Ablation node**: after baseline, an actor writes/runs ablations
  (drop/neutralize one pipeline block per run, ShortTrain budget), a
  deterministic step ranks block impact, and the ranking is injected into the
  Proposal prompt → targeted refinement instead of "change something".
- **P3 — Parallel candidates + ensembling**: K independent short-train
  trajectories (k saage remote targets, or sequential on one box), then an
  EnsembleActor merges top candidates (stacking/blending code it writes) and
  the ensemble is scored like any candidate.
- **P4 — Checkers as critics**: data-leakage critic (train/test contamination,
  target leakage) and data-usage critic (are all provided files actually
  used?) — cheap, prevents catastrophic invalid submissions; slot in exactly
  like existing critics.
- **P5 — Cross-competition memory**: persist research logs + kept-experiment
  diffs across runs; retrieve at propose time (ML-Master's lesson). Cheapest
  v1: a corpus dir the Proposal actor greps.

**Honest positioning:** SOTA Lite numbers (63–75%) are big-model, big-budget
runs. Our differentiated brag is **medals per dollar**: deepseek-class models
on $0.47–1.29/hr rented GPUs, fully reproducible from a public YAML flow.
Target: clearly beat the AIDE-class baselines (~17–26%) on Lite at <$5/comp,
then climb.

## 4. saage integration plan

**Shape: a saage flow + helper scripts + optional extra.** The solver's
competitiveness lives in prompts and deterministic helpers, not in mle-beast's
engine — greenfield_ml already proved the actor/critic→retry_loop,
hillclimb→counting_loop translation.

```
saage/
  pyproject.toml         # [project.optional-dependencies] kaggle-solver = ["mlebench", "kaggle", "pandas"]
  flows/kaggle_solver/
    flow.yaml            # comp_understanding → eda → baseline → counting_loop(hillclimb) → final_train → submit
    <skill dirs>/        # ported prompts: comp_understanding, propose, critic, implement, verify, ...
    prepare_comp.py      # wraps mlebench prepare/load_competition → task.md into shared store
    ablation.py          # P2: run ablation matrix, print BLOCK_RANKING=...
    grade.py             # wraps mlebench grade; prints MEDAL=gold|silver|bronze|none SCORE=...
    ensemble.py          # P3 deterministic scoring/merge support
  flows/kaggle_solver/README.md   # the brag page: results table, cost, repro commands
```

- `pip install -e ".[kaggle-solver]"` = deps only; the flow is data and ships
  regardless (running it without the extra fails fast in `prepare_comp.py`
  with "pip install saage[kaggle-solver]"). Sidecar achieved with zero
  engine hooks. Note `mlebench` installs from GitHub
  (`mlebench @ git+https://github.com/openai/mle-bench.git`), and preparing
  competition data needs Kaggle API credentials + accepted competition rules
  — `prepare_comp.py` checks and prints the exact fix.
- mle-beast keeps its own pipeline; nothing moves in the public repo. The
  internal `benchmark/` package stays as the reference implementation until
  the saage flow matches its nomad2018 result.

## 5. The port, stage by stage

The internal pipeline is PocketFlow actors/critics
(`mle-beast-internal/src/mle_beast/benchmark/`); saage expresses the same
graph as YAML steps over the shared store. The mapping, with deliberate
deltas marked **Δ**:

| Internal node(s) | saage construct | Notes |
|---|---|---|
| `runner.run_single_competition` setup (workspace, data symlink, description copy) | `command:` → `prepare_comp.py --comp {{ competition_id }} --data-dir {{ mlebench_data_dir }}` | Ports `competition.py` (`load_competition`, `build_task_description`). Writes `task.md` into the workspace, copies/symlinks data, prints `SAMPLE_COLS=… SAMPLE_ROWS=… TASK_READY=ok` for `set:` capture. Fails fast with install/prepare instructions when mlebench/data are missing. |
| `CompUnderstandingActor` + `Critic` | `retry_loop`: agent `comp_understanding` / agent `comp_understanding_critic` | Skill body = ported `COMP_UNDERSTANDING_SYSTEM_PROMPT` (incl. the all-modalities nag). Critic reads `competition_understanding.md`, ends `ACTION: pass|fail`. Keep the internal lesson: don't over-gate (run 4b relaxed this). |
| `DataAnalysisActor` + `Critic` | `retry_loop`: agent `eda` / agent `eda_critic` | EDA scripts via `run_command`; output `data_analysis.md`. `MPLBACKEND=Agg` baked into the skill instructions (run 5 lesson). |
| `GitSetupNode` | `command:` → `setup_competition.py --branch "{{ run_branch }}"` | Port of the git machinery (branch, snapshot commit, `.git/info/exclude` for `research_log.md`/`experiments.jsonl`/checkpoints, research-log seed). Modeled on lewm's `setup_experiment.py`, so it inherits the remote run-branch wiring for free. |
| `BaselineActor` + `SmokeTestCritic` | `retry_loop`: agent `build_baseline` / **Δ command smoke check** | Baseline skill ports `BASELINE_SYSTEM_PROMPT` + the train.py CLI contract (below). Smoke check becomes deterministic: `python -B -m pytest -q tests/ && echo "ACTION: pass" \|\| echo "ACTION: fail"` — needs engine addition E2. |
| `ShortTrainActor` + `TrainCritic` | **Δ `command:` train + agent `verify_training` check** | Internal ShortTrain is an LLM actor that launches training; saage runs it deterministically — possible because the baseline skill enforces a **train.py contract**: `--device {{ device }} --epochs N --data data/`, writes `training.log` + `checkpoints/`, and at exit appends `VAL_SCORE=<float>` to `eval_results.json`. The contract replaces the internal Finder pair AND the LLM val-score extractor (runs 6–7's pain) with a `set:` capture. `verify_training` (agent) reads the log tail for convergence sanity, `ACTION: pass|fail`. |
| `BaselineEvalNode` / `HillClimbEvalNode` | `command:` → `keep_or_revert.py --candidate {{ candidate_score }} --best {{ best_score }} --lower-is-better {{ lower_is_better }} …` | Direction-aware port (greenfield's helper + internal direction fixes `4c8093f`/`162e2e5`). Prints `BEST_SCORE= FAILURES= RESULT=` for captures; appends `experiments.jsonl` + research log; git commit/revert + `git clean -fd` (commit `1a24548`'s lessons). |
| Hill-climb loop control (`max_steps`, `max_consecutive_failures`, abandon) | `counting_loop` `max_iterations: {{ max_steps }}`, `exit_when: "consecutive_failures >= max_consecutive_failures or (target_set and best_better_than_target)"` | **Δ** The internal *abandon* edge (smoke keeps failing → new proposal) falls out naturally: implement retry_loop exhausts → train fails → candidate stays sentinel → revert + failure counter — the same "no single hiccup kills the run" property, expressed as data flow. `exit_when` needs a tiny helper-computed flag for direction-aware target comparison (set by `keep_or_revert.py` as `TARGET_MET=0|1`), keeping the predicate trivial. |
| `ProposalActor` + `ProposalCritic` | `retry_loop`: agent `propose` / agent `proposal_critic` | Propose prompt ports the step/best/consecutive-failures context block verbatim (it's all `{{ }}` template-able) + reads `research_log.md`. P2 later injects `BLOCK_RANKING` here. |
| `ImplementActor` | `retry_loop` action: agent `implement_experiment` (check = the smoke command) | Ports `IMPLEMENT_SYSTEM_PROMPT`; proposal arrives via `{{ current_proposal }}` capture (greenfield already does this). |
| `FinalTrainActor` + critic | `command:` train (`--epochs {{ final_epochs }}`) + agent `verify_training` | Same contract as ShortTrain; runs once after the loop. |
| `ReportNode` | agent `report_narrative` + `command:` `report.py` | Direct reuse of the greenfield pattern; embeds `data_analysis.md` + EDA plots (commit `d913ff8`). |
| `SubmissionActor` + `SubmissionCritic` | `retry_loop`: agent `make_submission` / **Δ command check** → `validate_submission.py` | Deterministic critic: columns + row count vs `sample_submission.csv`, NaN check; prints `ACTION: pass|fail` with the diff as feedback. Needs E2. |
| `grader.py` | `command:` → `grade.py` (optional final step; also used by the sweep driver) | Wraps `mlebench grade`; prints `MEDAL=… TEST_SCORE=…`. |

**What deliberately does NOT port:** discriminated-union tool calls (saage's
allow-listed `tools:` per skill is the coarser equivalent — revisit only if
tool hallucination shows up in practice); the SQLite/EventBus/dashboard layer
(saage's run summary + artifacts are the v1 story); the Finder actor/critic
pairs (replaced by the train.py/eval contract — strictly more deterministic).

### Shared store seed (flow.yaml `shared:` + `--set` overrides)

`competition_id`, `mlebench_data_dir`, `device` (auto via helper, overridable),
`metric_name`, `lower_is_better`, `target_score` (optional), `short_epochs: 15`,
`final_epochs: 100`, `max_steps: 30`, `max_consecutive_failures: 10`,
`best_score`/`candidate_score` sentinels, `consecutive_failures: 0`,
`run_branch` (remote-aware, like lewm). `artifacts:` declares
`[experiments.jsonl, research_log.md, submission.csv, report*.{md,html},
competition_understanding.md, data_analysis.md]` so remote sweeps sync the
right files.

### Engine additions (in build order)

- **E2 — `ACTION:` parsing for `command` steps** (CommandNode.post reads the
  last `ACTION:` from stdout, else "default"). Tiny, generic, and it's what
  makes deterministic loop checks (pytest smoke, submission validation)
  possible. *Blocks the port — do first.*
- **E1 — optional `web_search` harness tool** for P1 retrieval-grounded
  proposals. Allow-listed per skill; not required for the v1 port.
- **E3 (nice-to-have) — per-step model override** in flow.yaml (cheap model
  for EDA/critics, strong model for propose/implement). Pure cost lever.
- P3 parallelism intentionally needs **no engine change** — it's `saage
  remote` fan-out.

### Environment & data on remote nodes

- Workspace env: greenfield's `setup_env.py` (ml-frameworks stack) +
  `xgboost lightgbm catboost` (internal `runner.py` installs these as
  dev_tool_packages — several Lite comps are tabular).
- Competition data: `mlebench prepare --lite` once (needs Kaggle creds +
  accepted rules), then **stage prepared comps to the R2 bucket**
  (`datasets/mlebench/<comp_id>/`) so nodes pull at datacenter bandwidth with
  zero egress cost — same pattern as the lewm dataset. `--ws-setup` hook
  pulls the one competition the node needs.

### Milestones (each gates the next)

- **M0 — wiring:** flow hydrate-checks; offline ScriptedProvider integration
  test (mirror `tests/integration/test_guessing_game.py`) covering the loop,
  captures, and both deterministic checks. E2 lands with its own unit tests.
- **M1 — cheap live run:** one tabular Lite comp (`spooky-author` or
  `tabular-playground-*`) end-to-end locally (CPU or the Windows box),
  deepseek-class model. Produces a graded submission.
- **M2 — parity:** nomad2018 on a remote A6000/A10 via
  `saage remote handoff`; bar = internal solver's result (≥ above-median;
  silver = parity). Until M2 passes, the internal `benchmark/` package is
  the reference and stays untouched.
- **M3 — the sweep:** `bench.py` driver — spawn K targets, one handoff per
  competition (`--set competition_id=…`), poll via the R2 mirror, fetch,
  `mlebench grade`, emit the results table into a `BENCHMARK_RESULTS.md`-style
  journal in saage. First full Lite number = the launch brag.
- **M4+ — competitiveness features** from §3 in priority order (P1 retrieval,
  P2 ablation, P3 ensemble), each measured against the M3 baseline sweep.

### Work breakdown (rough order)

1. E2 engine change + tests (small PR).
2. `flows/kaggle_solver/` skeleton: flow.yaml + `prepare_comp.py` +
   `setup_competition.py` + `keep_or_revert.py` + `validate_submission.py`
   (ports of `competition.py`/`runner.py`/`hillclimb.py` pieces, helpers
   pattern from greenfield/lewm).
3. Skills: port the 8 system prompts from `benchmark/prompts.py` into
   skill.md bodies, adding the train.py/eval contract language.
4. `[kaggle-solver]` extra in pyproject + README brag page skeleton.
5. M0/M1, then M2 on a rented box, then `bench.py` + M3.

## 6. Decisions (resolved 2026-06-11)

1. **Lite only** for v1.
2. **DuckDuckGo for P1, with contamination guards** — the Lite comps are old
   Kaggle competitions whose solutions are public, so unconstrained search
   could fetch the answer and invalidate the result. Mechanical guards, not
   honor-system: (a) the search skill never receives the competition name/id,
   only a task characterization; (b) domain blocklist (kaggle.com, solution
   mirrors); (c) every query + fetched URL logged into run artifacts so
   results are auditable. Ship-first alternative if that still feels risky:
   a static, curated "model recipe card" corpus per task family (zero
   contamination), with guarded live search behind a flag.
3. **~$40–100/sweep budget approved**; cadence = after each milestone /
   feature lands, not scheduled.
4. **`BENCHMARK_RESULTS.md`-style journal in saage** (dashboard stays
   mle-beast-only).
5. **Contract over extraction**, eyes open: the score that drives keep/revert
   comes from a required `eval_results.json` write (deterministic capture),
   not LLM log-reading. Known fallback if agents fight the contract on weird
   comps (the internal runs 6–7 story): reintroduce the internal LLM score
   extractor as a small skill between train and keep_or_revert. M1/M2 decide.
