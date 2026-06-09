# Plan: Composable Deterministic Agentic Workflow Engine

## Context

Today you build automated workflows by composing **skills** (markdown, possibly with code) into **agent** files inside harnesses like Claude Code, Gemini, Codex, Copilot, or Windsurf. This works but is **non-deterministic and unreliable**:

- Nothing guarantees the harness executes the workflow as written. The LLM decides control flow, and it decides badly — e.g. an MLflow/squeue **poll step gets launched in the background and never returns**, so the agent stalls forever.
- The same workflow run twice gives different results; swapping models changes behavior entirely. Reliability hinges on how a given model interprets the agent file.

The goal: a **deterministic workflow engine** where *control flow is code, not model judgment*, but individual steps still use LLMs. The engine is built on **PocketFlow** (a ~100-line graph/shared-store framework) plus a **lightweight first-class harness** (file CRUD+exec and git tools) that LLM steps drive through a **native, provider-agnostic agent loop**. Workflows are authored in **YAML** and hydrated into PocketFlow flows. Existing **Claude-format markdown skills** are imported as-is.

### Decisions locked in (from user)
- **LLM execution:** build our own agent loop calling a model API directly, exposing *our* tools. No shelling out to external CLIs.
- **Provider:** provider-agnostic from day 1 — thin `LLMProvider` abstraction with **Anthropic** and **OpenAI** backends both wired up.
- **Skills:** reuse the existing Claude/Agent skill markdown format (YAML frontmatter + instruction body), imported unchanged.

> Naming: working package name `saage` (composable-workflow-engine). Final product name TBD — not a blocker.

---

## Architecture Overview

Three layers, bottom-up:

```
┌─────────────────────────────────────────────────────────────┐
│ 3. Workflow layer — YAML spec → hydrated PocketFlow Flow      │
│    primitives: retry_loop · polling_loop · counting_loop      │
├─────────────────────────────────────────────────────────────┤
│ 2. Agent layer — skills + provider-agnostic tool-use loop     │
│    AgentNode (PocketFlow Node) drives LLMProvider + Tools     │
├─────────────────────────────────────────────────────────────┤
│ 1. Harness layer — first-class tools                          │
│    files: read/write/edit/delete/list  · exec: run_command    │
│    git:   status/diff/branch/commit/log/add/checkout          │
└─────────────────────────────────────────────────────────────┘
   Foundation: PocketFlow (Node / Flow / shared store / actions)
```

**Key determinism principle:** the LLM only ever chooses *content* (what code to write, whether a review passes). It **never** chooses *control flow* — looping, polling, retry, and exit conditions are owned by Python/PocketFlow code. The "poll-launched-in-background-and-never-returns" failure becomes structurally impossible because polling is a `polling_loop` primitive the engine runs synchronously.

### How PocketFlow is used (grounded in its real API)
PocketFlow primitives we build on (verified from source):
- `Node(max_retries, wait)` with `prep(shared)/exec(prep_res)/post(shared,prep,exec)`. `post()` returns an **action string**; `max_retries`/`wait` only retry on **exceptions** (not semantic loops).
- `node_a >> node_b` (default action), `node_a - "action" >> node_b` (named action), and loop-back by pointing an action at an earlier node.
- `Flow(start=node)` orchestrates by following returned action strings; **a Flow is itself a Node**, so our composite primitives nest inside larger flows.
- Async variants (`AsyncNode`, `AsyncFlow`) exist; for v1 we keep the engine **synchronous** (polling uses `time.sleep`) — simpler and fully deterministic. Async is a later enhancement.

Our loop primitives are **not** PocketFlow's `max_retries` (that's exception-only). They are explicit subgraphs with a **counter and condition stored in the shared store**, guarded by a Python gate node.

---

## Component Design

### Layer 1 — Harness tools (`saage/tools/`)
A neutral `Tool` abstraction: `name`, `description`, JSON-schema `parameters`, and `run(**kwargs) -> ToolResult`. Tools are provider-neutral; each provider adapter translates the schema to Anthropic tool-use / OpenAI function-calling format.

- `files.py` — `read_file`, `write_file`, `edit_file` (exact-string replace), `delete_file`, `list_dir`. All paths confined to a configurable **workspace root** (sandbox guard; reject `..` escapes).
- `exec.py` — `run_command(cmd, timeout)`; captures stdout/stderr/exit code; enforced timeout so a hung command can't stall the workflow.
- `git.py` — thin wrappers shelling `git` directly (no `gh`, no external auth): `git_status`, `git_diff`, `git_branch`, `git_commit`, `git_log`, `git_add`, `git_checkout`. Run inside the workspace root.
- `registry.py` — `ToolRegistry` that collects tools and emits per-provider tool specs; a skill may restrict which tools it can use.

### Layer 2 — Provider abstraction + agent loop (`saage/llm/`, `saage/agent/`)
- `saage/llm/base.py` — `LLMProvider` interface: `complete(messages, tools, model, **opts) -> LLMResponse` where `LLMResponse` carries assistant text + a normalized list of `ToolCall(name, args, id)`.
- `saage/llm/anthropic.py` — Anthropic Messages API: maps neutral tools → `tools=[...]`, handles `tool_use`/`tool_result` content blocks.
- `saage/llm/openai.py` — OpenAI Chat Completions: maps neutral tools → `functions`/`tools`, handles `tool_calls`/`tool` role messages.
- `saage/llm/scripted.py` — **`ScriptedProvider`** that replays a canned sequence of responses/tool-calls. Critical for deterministic tests (no network, exact reproducibility).
- `saage/agent/loop.py` — `AgentLoop.run(instructions, context, tool_registry) -> AgentResult`: standard tool-use loop — send messages, execute returned tool calls against the registry, append results, repeat until the model emits no tool calls **or** `max_steps` is hit. Bounded so it always terminates.

### Layer 2.5 — Skills (`saage/skills/`)
- `loader.py` — parse **Claude-format markdown**: YAML frontmatter (`name`, `description`, optional `tools:`, optional `model:`) + markdown body (the instructions). Returns a `Skill` object. Bodies are used verbatim as the agent's instructions, so your existing skills import unchanged.
- A skill resolves to an `AgentNode` at hydration time.

### Layer 3 — Nodes + primitives (`saage/nodes/`)
Concrete PocketFlow `Node` subclasses; all read/write the shared store.
- `AgentNode` — `prep` builds context from shared + skill; `exec` runs `AgentLoop` with a skill's instructions and the tool registry; `post` writes the result to shared and returns an action.
- `CommandNode` — deterministic shell step (no LLM); useful as poll/setup/teardown steps.
- **Primitive factories** (each returns a PocketFlow `Flow` that nests anywhere):
  - `retry_loop(action_node, check_node, max_iterations)` — wiring: `action >> check`; `check - "fail" >> action` (re-invoke with failure context injected into shared); `check - "pass" >> <exit>`. A Python gate inside `check.post` increments `shared["_retry"][id]` and forces exit (action `"pass"`) at `max_iterations`. *Use case: code_implement → code_review loop.*
  - `polling_loop(poll_node, interval_seconds, max_wait_seconds, status_key)` — `poll >> classify`; `classify - "running" >> wait`; `wait >> poll` (a `WaitNode` doing `time.sleep(interval)`); `classify - "complete" >> <exit>`; `classify - "failed" >> <fail>`. Hard wall-clock cap via `max_wait_seconds`. *Use case: submit to Slurm, poll squeue until done/failed.*
  - `counting_loop(body_nodes, max_iterations, exit_when)` — chain `body[0] >> ... >> body[n] >> gate`; `gate - "continue" >> body[0]`; `gate - "exit" >> <exit>`. Gate evaluates a safe `exit_when` predicate over the shared store **and** the iteration counter. *Use case: ML auto-research loop until `accuracy >= target_accuracy` or `max_iterations`.*

### Layer 3.5 — YAML hydration (`saage/workflow/`)
- `schema.py` — Pydantic models validating the YAML (clear errors on malformed specs).
- `hydrate.py` — walk the validated spec, resolve skills → `AgentNode`s, build primitive sub-flows, wire `>>`/`- "action" >>` transitions, return a runnable top-level `Flow`.
- `runner.py` — `run_workflow(path, initial_shared) -> shared`: load YAML, hydrate, seed shared store, `flow.run(shared)`, return final shared. Structured logging of every node entry/exit/action for observability.
- `saage/cli.py` — `saage run workflow.yaml [--set key=value ...]` entry point.

### Proposed YAML schema (illustrative)
```yaml
name: ml-auto-research
description: Iteratively improve a model until target accuracy or max iters.
provider: { type: anthropic, model: claude-opus-4-8 }   # or type: openai
skills:
  - ./skills/implement.md
  - ./skills/review.md
  - ./skills/train.md
shared:                       # seeds the shared store
  target_accuracy: 0.92
workflow:
  - id: research
    type: counting_loop
    max_iterations: 10
    exit_when: "accuracy >= target_accuracy"
    body:
      - id: implement_review
        type: retry_loop
        max_iterations: 5
        action: { type: agent, skill: implement }
        check:  { type: agent, skill: review }   # post() -> pass|fail
      - id: train
        type: command
        run: "sbatch train.sh"                     # writes job_id to shared
      - id: wait_for_training
        type: polling_loop
        interval_seconds: 30
        max_wait_seconds: 7200
        poll:   { type: command, run: "squeue -j {{ job_id }}" }
        status: { type: agent, skill: classify_job }  # -> running|complete|failed
```

---

## Build Milestones

Vertical slices — each ends with something runnable and tested.

1. **M1 — Harness + tools.** `Tool`/`ToolRegistry`, file + exec + git tools, workspace sandbox. Unit tests against a temp dir + temp git repo.
2. **M2 — Provider abstraction + agent loop.** `LLMProvider`, Anthropic + OpenAI adapters, `ScriptedProvider`, `AgentLoop`. Tests drive the loop with `ScriptedProvider` (zero network) asserting tool calls fire and the loop terminates.
3. **M3 — Skills + nodes.** Skill loader (Claude markdown), `AgentNode`, `CommandNode`. Test: a one-skill agent node edits a file via the loop end-to-end with `ScriptedProvider`.
4. **M4 — Primitives.** `retry_loop`, `polling_loop`, `counting_loop` as PocketFlow flow factories. Tests with fake/scripted nodes assert loop counts, exit conditions, polling termination, and the failure-context re-injection path.
5. **M5 — YAML hydration + CLI.** Schema, hydrator, runner, `saage run`. End-to-end test: a small YAML workflow runs to completion with `ScriptedProvider`.
6. **M6 — Real-workflow validation.** Port your ML auto-research workflow; one live smoke run against a real provider. Docs + examples.

PocketFlow itself is added as a dependency (`pip install pocketflow`); we do **not** fork it — we compose on top.

---

## Proposed file layout
```
composable-workflow-engine/
  pyproject.toml            # deps: pocketflow, anthropic, openai, pydantic, pyyaml, jinja2
  saage/
    __init__.py
    cli.py
    tools/      {base,files,exec,git,registry}.py
    llm/        {base,anthropic,openai,scripted}.py
    agent/      loop.py
    skills/     loader.py
    nodes/      {agent_node,command_node,wait_node}.py
    primitives/ {retry_loop,polling_loop,counting_loop}.py
    workflow/   {schema,hydrate,runner}.py
  examples/     ml-auto-research/{workflow.yaml,skills/*.md}
  tests/        test_tools_*, test_agent_loop, test_primitives_*, test_hydrate, test_e2e
```

---

## Verification
- **Per-milestone unit tests (pytest):** tools against temp dir + temp git repo; agent loop and all primitives driven by `ScriptedProvider` so runs are network-free and bit-reproducible.
- **Determinism check:** run an E2E workflow twice with `ScriptedProvider` and assert identical shared-store output and identical node-execution trace — proves control flow is code-owned, not model-owned.
- **Termination guarantees:** tests assert `polling_loop` honors `max_wait_seconds`, `retry_loop`/`counting_loop` honor `max_iterations`, and `run_command` honors its timeout — i.e. no path can hang (directly addressing the "poll never returns" failure).
- **Live smoke (M6):** `saage run examples/ml-auto-research/workflow.yaml` against a real provider; confirm the loop iterates, polling exits on job completion, and the exit condition fires.

## Open questions / deferred
- Async engine (`AsyncFlow`) for concurrent steps — deferred; v1 is synchronous.
- Checkpoint/resume of a workflow's shared store across crashes — deferred to a later milestone.
- Final product name.
