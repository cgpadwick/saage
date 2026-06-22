# Report-as-Agent — Design

**Status:** approved (2026-06-21)

## Goal

Replace the deterministic `report.py` (+ `make_plot`) and the `report_narrative`
agent with a single **`report` agent** that writes a self-contained
`report.html`. The agent reads whatever upstream files exist and adapts —
fixing the brittleness of a hardcoded report.py that makes rigid assumptions
about the format/keys of `experiments.jsonl` / `research_log.md` / config files.

PR #18 review (the trigger): *"this is all just hardcoded brittle crap that's
going to break as soon as the format shifts"* / *"why not do this with an agent,
that's what I was envisioning."*

Scope: `flows/greenfield_ml` and `contrib/lewm_hillclimb_guided` (PR #18, where
report.py lives). `flows/kaggle_solver` adopts the same agent as a fast-follow on
PR #19 (its report was deferred #6 — now defined by this design).

## What the report must contain

The user's requirements, verbatim intent:
1. A **table summarizing each experiment** (step, what changed, candidate vs
   best score, kept/reverted, commit).
2. An **overview** of the run (task, baseline, best, target met?, #experiments).
3. A **prose description of the winning experiment**.
4. A **graph of hill-climb performance** over the run.

## Decisions

- **One agent, not agent+script.** The `report` agent produces the whole
  `report.html` (prose + table + chart). Removes `report.py`, `make_plot`, and
  the separate `report_narrative` skill (its prose role folds into `report`).
- **Graph = inline SVG, authored by the agent.** No matplotlib, no Chart.js, no
  CDN, no vendored library. The agent emits an `<svg>` line chart directly:
  best-so-far polyline + kept(green)/reverted(red) candidate markers, axes,
  gridlines, legend, title. Rationale: zero deps, fully offline, single portable
  self-contained file, vector-crisp, and 100% agent-built. (Rejected: Chart.js —
  CDN breaks offline, vendoring a ~200KB blob + an inliner is unsavory;
  matplotlib PNG — adds a dep + a run step, raster not vector.)
- **Self-contained HTML.** Inline CSS + inline SVG only; no external resources.
  Opens anywhere with no network.

## The `report` agent skill

`flows/<flow>/report/skill.md` (one per flow, per the repo's self-contained-flow
convention):

- **tools:** `[read_file, write_file, run_command]` (run_command so it can `ls`/
  inspect the workspace to discover what artifacts exist — flexibility).
- **description:** carries the task/metric context the flow already passes to
  `report_narrative` (e.g. `{{ task }}`, target, direction).
- **instructions:** read `experiments.jsonl` (one JSON object per line),
  `research_log.md`, and the winning config/model files (whatever exists); then
  write `report.html` with: the overview, the per-experiment table, the
  winning-experiment prose, and the inline-SVG hill-climb chart. The skill gives
  a concrete **SVG styling recipe** (viewBox ~720×360, margins, map step→x and
  score→y over the data's actual range, best-so-far `<polyline>`, kept/revert
  `<circle>` markers, axis ticks + gridlines, a legend, a title) so output is
  consistent and professional while the agent stays flexible on the data.
  Degrade gracefully: nan/`None` scores are skipped from the line; an empty
  ledger still yields a valid (if sparse) report. Self-contained HTML only.

## Flow changes (per flow)

- Remove the `report_narrative` agent step and the `report` command step (which
  ran `report.py`); replace with a single `report` agent step.
- The final commit step adds `report.html` (drop `report_narrative.md`).
- `artifacts:` keeps `report.html`; drop `report_narrative.md`.

## Files removed

- `flows/greenfield_ml/report.py`, `flows/greenfield_ml/report_narrative/skill.md`
- `contrib/lewm_hillclimb_guided/report.py`,
  `contrib/lewm_hillclimb_guided/report_narrative/skill.md`

(No dedicated `report.py` unit tests exist to remove.)

## Testing strategy

- `tests/test_flows_hydrate.py` (existing) guards that each flow + the new
  `report` skill hydrate. greenfield/lewm have no full end-to-end integration
  test (too heavy), so hydrate is the offline guard.
- The report agent's *output quality* (valid HTML, well-formed SVG) is an LLM
  content concern, not unit-testable offline; it's validated by a live smoke run
  (manual), consistent with how the other agent skills are exercised.
- kaggle (fast-follow on PR #19) DOES have `tests/integration/test_kaggle_solver.py`
  running the flow with scripted turns — adopting the `report` agent there will
  require swapping the scripted `report_narrative` turn for a `report` turn.

## Out of scope

- kaggle implementation (separate, on PR #19, same agent).
- Any change to the ledger/keep_or_revert (done in #18 already).
