# Report-as-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the brittle hardcoded `report.py` (+ `make_plot`) and the `report_narrative` agent in the greenfield_ml and lewm flows with a single LLM `report` agent that writes a self-contained `report.html` (overview, experiment table, winning-experiment prose, inline-SVG hill-climb chart).

**Architecture:** One `report` agent per flow (repo convention = self-contained flows). The agent reads whatever upstream files exist (`experiments.jsonl`, `research_log.md`, winning config/model) and emits one portable HTML file with an agent-authored inline SVG chart — no matplotlib, no Chart.js, no CDN, no vendored library.

**Tech Stack:** saage flow engine (`flow.yaml` + `skill.md`), pytest hydrate guard.

## Global Constraints

- Graph = agent-authored **inline SVG**: X = experiment number, Y = performance; **keeps = green filled dots, reverts = red ✕ marks** (distinct shapes, not just color); best-so-far line; axes + gridlines + legend + title; **selective annotations** of the biggest win(s) + several notable failures (experiment # + short description), NOT every point — keep it uncluttered (~4–8 callouts).
- Report structure, in order: (1) outcome/winner prose up front (the winning experiment + the architecture/details that worked), (2) per-experiment table (change, candidate vs best, kept/reverted, commit), (3) the SVG hill-climb graph.
- Persona: "You are an excellent scientific report writer… beautiful, concise, informative." Be ACCURATE — only real scores/experiments, never invent.
- Self-contained HTML: inline CSS + inline SVG ONLY, no external resources; renders fully offline.
- Per-flow skills carry that flow's context vars + winning-file names (greenfield: `model.py`, `target_accuracy`, `lower_is_better`, field `kept`; lewm: `config/train/lewm.yaml` + `config/train/model/lewm.yaml`, `target_success`, field `status`).
- Tests: `tests/test_flows_hydrate.py` is the offline guard (greenfield/lewm have no full integration test). Run with `python -m pytest`, never bare `pytest`.

---

### Task 1: greenfield_ml report agent

**Files:**
- Create: `flows/greenfield_ml/report/skill.md`
- Modify: `flows/greenfield_ml/flow.yaml`
- Delete: `flows/greenfield_ml/report.py`, `flows/greenfield_ml/report_narrative/skill.md`
- Test: `tests/test_flows_hydrate.py` (existing — auto-discovers; no edit)

**Interfaces:**
- Consumes: `experiments.jsonl` rows (`step, parent_step, candidate, best, kept, commit_sha, files_changed, summary, proposal`), `research_log.md`, `model.py`/`train.py`.
- Produces: `report.html` (self-contained).

- [ ] **Step 1: Create the report agent skill**

Create `flows/greenfield_ml/report/skill.md`:

```markdown
---
name: report
description: |
  Task: {{ task }}
  Final best test accuracy: {{ best_score }} (target {{ target_accuracy }}, higher is better).
  Write the final HTML research report for this ML auto-research run.
tools: [read_file, write_file, run_command]
---
SKILL_ID: report

You are an excellent scientific report writer. Generate a beautiful, concise, and
informative scientific report as a single self-contained `report.html` from the
inputs below. Be ACCURATE — use only the real scores and experiments from the
files; never invent results.

## Inputs (read these first)
- `experiments.jsonl` — one experiment per line. Fields: `step`, `parent_step`,
  `candidate` (this experiment's test accuracy), `best` (running best after it),
  `kept` (true = it improved the score and was committed; false = reverted),
  `commit_sha`, `files_changed`, `summary` (one-paragraph change summary),
  `proposal` (full proposal text).
- `research_log.md` — the running narrative of the run.
- the final `model.py` (the best architecture that survived) and `train.py` — the
  details of what ended up working. `git log --oneline` lists the kept commits.

## The report (`report.html`) must contain, IN THIS ORDER

1. **Outcome — up front.** A short prose section naming the winning result: the
   final best accuracy vs the baseline and the target ({{ target_accuracy }}), and
   a clear description of the winning experiment(s) — the architecture/approach and
   key details that ended up working (read `model.py` for the REAL architecture).

2. **Experiment table.** One row per experiment: step, a short description of the
   change (use `summary`), candidate vs best accuracy, KEPT or REVERTED, short commit.

3. **Hill-climb graph — an inline `<svg>`** (no external libraries):
   - X axis = experiment number, Y axis = test accuracy.
   - Plot best-so-far as a line; mark each experiment: **keeps = green filled
     dots, reverts = red ✕ marks** (a red X — two crossed red lines — a distinct
     shape, not just color).
   - Include axis ticks, light gridlines, a legend (green dot = kept, red ✕ =
     reverted), and a title.
   - **Annotate selectively** — do NOT label every point (keep it uncluttered):
     call out the biggest win(s) and several notable failures with the experiment
     number + a short description (from `summary`) on a small `<text>` label with a
     thin leader line. Aim for ~4–8 annotations total.

## Style
Beautiful and professional but concise. Self-contained: inline CSS + inline SVG
ONLY — no CDNs, no external files, no `<img>` to disk. The file MUST render fully
offline. Clean readable layout (headings, a styled table, the chart).

Write ONLY `report.html`. Finish with a one-line confirmation.
```

- [ ] **Step 2: Replace the report steps in the flow**

In `flows/greenfield_ml/flow.yaml`, replace the final report block:

```yaml
  # ---- final research report ----
  # an LLM writes the prose summary (how the model works + winning experiments), then a
  # deterministic script assembles the HTML (summary + plot + table + architecture).
  - { id: report_narrative, type: agent, skill: report_narrative, max_steps: 12 }
  - id: report
    type: command
    run: '{{ python }} "{{ flow_dir }}/report.py" --task "{{ task }}" --target "{{ target_accuracy }}" --lower-is-better "{{ lower_is_better }}"'
```

with:

```yaml
  # ---- final research report ----
  # one LLM agent writes the whole self-contained report.html: outcome/winner prose,
  # per-experiment table, and an inline-SVG hill-climb chart. Reads whatever upstream
  # files exist (flexible — no hardcoded schema assumptions).
  - { id: report, type: agent, skill: report, max_steps: 25 }
```

- [ ] **Step 3: Update the artifacts list**

In `flows/greenfield_ml/flow.yaml`, change the artifacts line (currently `artifacts: [experiments.jsonl, research_log.md, eval_results.json,` / `            report.html, report_narrative.md]`) to drop `report_narrative.md`:

```yaml
artifacts: [experiments.jsonl, research_log.md, eval_results.json, report.html]
```

- [ ] **Step 4: Delete the obsolete files**

```bash
git rm flows/greenfield_ml/report.py flows/greenfield_ml/report_narrative/skill.md
```

- [ ] **Step 5: Confirm no dangling references**

Run: `grep -rn "report_narrative\|report\.py" flows/greenfield_ml/`
Expected: NO matches (the flow now references only the `report` agent skill; the comment mentions "report.html" which is fine — verify there is no `report.py` invocation or `report_narrative` step/skill left).

- [ ] **Step 6: Verify the flow hydrates**

Run: `python -m pytest tests/test_flows_hydrate.py -q`
Expected: PASS (greenfield_ml hydrates with the new `report` skill, no missing-skill error).

Also: `python -c "from saage.hydrate import build_flow; build_flow('flows/greenfield_ml/flow.yaml', provider=object(), workspace='/tmp/x'); print('ok')"`
Expected: `ok`.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all green (no test imported report.py; removing it + report_narrative breaks nothing).

- [ ] **Step 8: Commit**

```bash
git add flows/greenfield_ml/report/skill.md flows/greenfield_ml/flow.yaml
git commit -m "feat(greenfield): replace report.py with an LLM report agent (inline-SVG chart)"
```

---

### Task 2: lewm report agent

**Files:**
- Create: `contrib/lewm_hillclimb_guided/report/skill.md`
- Modify: `contrib/lewm_hillclimb_guided/flow.yaml`
- Delete: `contrib/lewm_hillclimb_guided/report.py`, `contrib/lewm_hillclimb_guided/report_narrative/skill.md`
- Test: `tests/test_flows_hydrate.py` (existing — no edit)

**Interfaces:**
- Consumes: `experiments.jsonl` rows (`step, parent_step, candidate, best, status, commit_sha, files_changed, summary, proposal` — note lewm uses `status` = "keep"/"revert", not `kept`), `research_log.md`, `config/train/lewm.yaml`, `config/train/model/lewm.yaml`.
- Produces: `report.html` (self-contained).

- [ ] **Step 1: Create the report agent skill**

Create `contrib/lewm_hillclimb_guided/report/skill.md`:

```markdown
---
name: report
description: |
  Task: {{ task }}
  Final best success_rate: {{ best_score }} (target was {{ target_success }}, higher is better).
  Write the final HTML report for this LeWM hill-climb run.
tools: [read_file, write_file, run_command, git_log]
---
SKILL_ID: report

You are an excellent scientific report writer. Generate a beautiful, concise, and
informative scientific report as a single self-contained `report.html` from the
inputs below. Be ACCURATE — use only the real scores and experiments from the
files; never invent results.

## Inputs (read these first)
- `experiments.jsonl` — one experiment per line. Fields: `step`, `parent_step`,
  `candidate` (this experiment's success_rate), `best` (running best after it),
  `status` ("keep" = it improved the score and was committed; "revert" = did not),
  `commit_sha`, `files_changed`, `summary` (one-paragraph change summary),
  `proposal` (full proposal text).
- `research_log.md` — the goal, the paper's reference numbers, and the run narrative.
- `config/train/lewm.yaml` and `config/train/model/lewm.yaml` — the winning
  configuration that survived (the details of what worked). `git_log` lists the
  kept commits (`saage: keep experiment, ...`).

## The report (`report.html`) must contain, IN THIS ORDER

1. **Outcome — up front.** A short prose section naming the winning result: the
   baseline success_rate, the final best vs the target ({{ target_success }}), and
   a clear description of the winning experiment(s) — the configuration/approach and
   key details that ended up working (read the two config YAMLs for the REAL winning
   settings).

2. **Experiment table.** One row per experiment: step, a short description of the
   change (use `summary`), candidate vs best success_rate, KEPT or REVERTED, short commit.

3. **Hill-climb graph — an inline `<svg>`** (no external libraries):
   - X axis = experiment number, Y axis = success_rate.
   - Plot best-so-far as a line; mark each experiment: **keeps (status "keep") =
     green filled dots, reverts (status "revert") = red ✕ marks** (a red X — two
     crossed red lines — a distinct shape, not just color).
   - Include axis ticks, light gridlines, a legend (green dot = kept, red ✕ =
     reverted), and a title.
   - **Annotate selectively** — do NOT label every point (keep it uncluttered):
     call out the biggest win(s) and several notable failures with the experiment
     number + a short description (from `summary`) on a small `<text>` label with a
     thin leader line. Aim for ~4–8 annotations total.

## Style
Beautiful and professional but concise. Self-contained: inline CSS + inline SVG
ONLY — no CDNs, no external files, no `<img>` to disk. The file MUST render fully
offline. Clean readable layout (headings, a styled table, the chart).

Write ONLY `report.html`. Finish with a one-line confirmation.
```

- [ ] **Step 2: Replace the report steps in the flow**

In `contrib/lewm_hillclimb_guided/flow.yaml`, replace this block:

```yaml
  # ---- final report: LLM narrative, then the deterministic HTML report ----
  - { id: report_narrative, type: agent, skill: report_narrative, max_steps: 15 }
  - id: report
    type: command
    run: '{{ python }} "{{ flow_dir }}/report.py" --task "{{ task }}" --target {{ target_success }}'
  - id: report_commit
    type: command
    run: 'git -c user.email=saage@local -c user.name=saage add research_log.md report_narrative.md report.html && git -c user.email=saage@local -c user.name=saage commit -m "saage: hillclimb report"'
```

with:

```yaml
  # ---- final report: one LLM agent writes the whole self-contained report.html
  # (outcome/winner prose, per-experiment table, inline-SVG hill-climb chart),
  # then commit it. Reads whatever upstream files exist (no hardcoded schema).
  - { id: report, type: agent, skill: report, max_steps: 25 }
  - id: report_commit
    type: command
    run: 'git -c user.email=saage@local -c user.name=saage add research_log.md report.html && git -c user.email=saage@local -c user.name=saage commit -m "saage: hillclimb report"'
```

- [ ] **Step 3: Update the artifacts list**

In `contrib/lewm_hillclimb_guided/flow.yaml`, set the artifacts line to drop `report_narrative.md` and ensure `report.html` is present:

```yaml
artifacts: [experiments.jsonl, research_log.md, report.html]
```

- [ ] **Step 4: Delete the obsolete files**

```bash
git rm contrib/lewm_hillclimb_guided/report.py contrib/lewm_hillclimb_guided/report_narrative/skill.md
```

- [ ] **Step 5: Confirm no dangling references**

Run: `grep -rn "report_narrative\|report\.py" contrib/lewm_hillclimb_guided/`
Expected: NO matches (only the `report` agent skill remains; verify no `report.py` invocation or `report_narrative` step/skill is left).

- [ ] **Step 6: Verify the flow hydrates**

Run: `python -m pytest tests/test_flows_hydrate.py -q`
Expected: PASS (lewm_hillclimb_guided hydrates with the new `report` skill).

Also: `python -c "from saage.hydrate import build_flow; build_flow('contrib/lewm_hillclimb_guided/flow.yaml', provider=object(), workspace='/tmp/x'); print('ok')"`
Expected: `ok`.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add contrib/lewm_hillclimb_guided/report/skill.md contrib/lewm_hillclimb_guided/flow.yaml
git commit -m "feat(lewm): replace report.py with an LLM report agent (inline-SVG chart)"
```

---

### Final verification

- [ ] **Full suite green**

Run: `python -m pytest -q`
Expected: all green (310-ish passed, 7 skipped); no references to `report.py`/`report_narrative` remain in either flow.

- [ ] **Both flows hydrate standalone**

Run: `python -c "from saage.hydrate import build_flow; [build_flow(f, provider=object(), workspace='/tmp/x') for f in ('flows/greenfield_ml/flow.yaml','contrib/lewm_hillclimb_guided/flow.yaml')]; print('ok')"`
Expected: `ok`.

## Notes for the controller

- A live smoke run is the real validation of report quality (valid HTML + well-formed SVG) — not unit-testable offline. Optional: after merge, regenerate a report from an existing `experiments.jsonl` by running the `report` agent against a finished workspace.
- kaggle_solver adopts the same `report` agent as a fast-follow on PR #19 (its report was the deferred #6); that will also require swapping the scripted `report_narrative` turn for a `report` turn in `tests/integration/test_kaggle_solver.py`.
