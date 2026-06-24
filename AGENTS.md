# AGENTS.md — building flows in `saage`

This file is for **coding agents**. Read it (not the whole codebase) to author new
flows and skills for this engine. It is complete enough to build a working flow
from scratch; drop into the source only when you need an internal detail.

## Mental model (read this first)

`saage` is a **deterministic** workflow engine. *Control flow* — loops, retries,
polling, exit conditions, ordering — is owned by **code/YAML**, never by an LLM's
judgment. LLMs are used only **inside a step** to produce *content* (write code,
write SQL, review a diff, summarize). So when you design a flow you are deciding:

- which steps are **deterministic** (`command`) vs **LLM** (`agent`), and
- how steps are wired with the three **loop primitives**.

A run shares one mutable dict, the **shared store**. Steps read inputs from it via
`{{ templates }}`, and write outputs back into it via `set:` regex captures. All
loop state lives in the shared store (the engine shallow-copies nodes each step,
so nothing else persists).

## Repo map

| Path | What |
|---|---|
| `flows/<name>/flow.yaml` | a flow: provider + shared seed + the `workflow` step list |
| `flows/<name>/<skill>/skill.md` | one skill = frontmatter + instruction body |
| `flows/<name>/<skill>/*.py` | optional helper scripts a step runs (see *Helpers*) |
| `saage/hydrate.py` | YAML → runnable flow (the schema authority) |
| `saage/nodes.py` | `AgentNode`, `CommandNode`, `render()`, loop guards |
| `saage/primitives.py` | `retry_loop`, `polling_loop`, `counting_loop` |
| `saage/tools.py` | the harness tools (file CRUD, `run_command`, git) |
| `saage/skills.py` | how `skill.md` is parsed |

Existing flows are the best templates: `story_writer` (counting_loop),
`fix_failing_test` (retry_loop), `poll_job` (polling_loop), `guessing_game`
(counting_loop + `exit_when` + shared feedback), `greenfield_ml` (everything).

## `flow.yaml` reference

```yaml
provider: { type: openrouter, model: "deepseek/deepseek-v4-flash" }   # required
workspace: /tmp/saage_run        # optional: tool/command cwd. default = the flow dir
venv: .venv                    # optional: auto-activated for commands once it exists
artifacts: [experiments.jsonl, "report*.html"]   # optional: workspace files/globs
                               # `saage remote` syncs back; ignored by local runs
shared:                        # optional: initial shared-store values
  question: "..."
  target_accuracy: 0.97
workflow:                      # required: an ordered list of steps
  - <step>
  - <step>
```

- `provider.type` ∈ `anthropic | openai | openrouter | local`; optional
  `retry: { max_attempts, base_delay }` sub-block. Override at run time with
  `--provider/--model/--base-url`.
- `workspace`, `venv`, `flow_dir`, and `python` are auto-seeded into the shared
  store, so `{{ workspace }}` / `{{ flow_dir }}` / `{{ venv }}` / `{{ python }}`
  are available in templates. `python` is the interpreter launcher for helper
  scripts (`python3` on POSIX, `python` on Windows — there is no `python3.exe`).

## Step types (exact YAML)

**`agent`** — run an LLM skill with the harness tools.
```yaml
- { id: write_query, type: agent, skill: write_query,
    set: { score: "SCORE=([0-9.]+)" },   # optional: capture from the agent's final text
    max_steps: 20 }                       # optional tool-call budget (default 20)
```

**`command`** — a deterministic shell step (no LLM). `run` is templated; cwd = workspace.
```yaml
- { id: train, type: command, run: "python train.py --epochs {{ train_epochs }}",
    set: { job_id: "job (\\d+)" } }       # optional capture from stdout/stderr
```

**`retry_loop`** — `action → check`; on `fail` loop back to `action` *with the
checker's feedback injected*, until `pass` or `max_iterations`.
```yaml
- id: do_it
  type: retry_loop
  max_iterations: 4
  action: { id: implement, type: agent, skill: implement }
  check:  { id: verify,    type: agent, skill: verify }   # must end with ACTION: pass|fail
```

**`polling_loop`** — `poll → status`; on `running` wait `interval_seconds` and poll
again, until `complete`/`failed` or the `max_wait_seconds` wall-clock cap.
```yaml
- id: wait
  type: polling_loop
  interval_seconds: 10
  max_wait_seconds: 600
  poll:   { id: poll,     type: command, run: "python status.py {{ job_id }}" }
  status: { id: classify, type: agent,   skill: classify }  # ACTION: running|complete|failed
```

**`counting_loop`** — run a body of steps repeatedly until `max_iterations` or the
`exit_when` predicate is true.
```yaml
- id: hillclimb
  type: counting_loop
  max_iterations: 12
  exit_when: "best_score >= target_accuracy"   # optional: a Python expr over shared
  body:
    - { id: propose,   type: agent,   skill: propose }
    - { id: evaluate,  type: command, run: "python eval.py" }
    - { id: keep,      type: command, run: "python keep_or_revert.py" }
```

Loops nest: an `action`/`check`/`body` entry can itself be a loop. A nested loop's
counter is reset each time the outer loop re-enters it. (Resume caveat: `saage
resume` re-enters only at the *outermost* loop, so a crash redoes the whole
in-progress outer iteration and reruns nested inner loops from scratch — see
"Resumable runs / restart-safe iterations" under Conventions & gotchas.)

## Skills (`skill.md`)

```markdown
---
name: write_query                       # optional; defaults to the directory name
description: One line. Becomes the agent's TASK (user message). Templated.
tools: [read_file, write_file, run_command]   # optional allow-list; omit = all tools
---
SKILL_ID: write_query                   # optional marker used by the test harness

You are a careful analyst. The question is:

    {{ question }}

...step-by-step instructions (this body is the system prompt; also templated)...
End your reply with `ACTION: pass` or `ACTION: fail`.
```

Key facts:

- **Two surfaces reach the model**: the `description` (→ the *task*/user message)
  and the **body** (→ the system prompt). **Both are Jinja-templated** from the
  shared store — put `{{ question }}` wherever it reads best. (Undefined name →
  `""` + a logged warning; wrap a literal brace in `{% raw %}…{% endraw %}`.)
- **`tools:`** restricts which harness tools this skill may call. Omit for all.
- **`ACTION:` convention** — a skill used as a loop `check`/`status` must end its
  reply with one action keyword; the engine reads the *last* `ACTION: <word>`:
  - `retry_loop` check → `ACTION: pass` (done) or `ACTION: fail` (retry; this
    reply becomes the feedback fed to the next `action` attempt).
  - `polling_loop` status → `ACTION: running | complete | failed`.
  - `pass`/`complete`/`exit`/`stop` normalize to success; `failed` propagates out
    of the subflow so an outer flow can branch on it.

## The shared store: templates, captures, exit_when

- **Read** values: `{{ var }}` in a `command` `run`, a skill `description`, or a
  skill body. Nested access works: `{{ results['poll']['stdout'] }}`.
- **Write** values: `set: { key: "regex" }` on any step. The regex is searched
  against the step's output; the **last** match wins; capture group 1 if present
  else the whole match; numeric strings are coerced to int/float for predicates.
- **`exit_when`** (counting_loop): a Python boolean expression evaluated over the
  shared store with **no builtins** (e.g. `best_score >= target_accuracy`,
  `feedback == 'correct'`). An undefined name logs a warning and counts as false.

## Harness tools

`read_file`, `write_file`, `append_file`, `edit_file` (replace an exact substring
that occurs once), `delete_file`, `run_command`, git (`git_status`, `git_diff`,
`git_add`, `git_commit`, `git_branch`, `git_checkout`, `git_log`), and
`web_search`. Plus one **opt-in** tool, `ask_user` (granted only when a skill
lists it in `tools:`).

- **File tools are sandboxed** to the workspace (`..`/absolute escapes rejected).
- **`web_search`** (`query`, `max_results=5`, clamped to [1, 20]) returns top
  results (title/url/snippet). Keyless by default via DuckDuckGo
  (`pip install saage[search]`); set `TAVILY_API_KEY` or `BRAVE_API_KEY` for a
  reliable keyed backend, or pin one with `SAAGE_SEARCH_BACKEND=ddg|tavily|brave`
  (default `auto`). Any failure (no key/lib, rate-limit, network) returns an
  `ERROR:` string, never crashing the run.
  - **Network egress:** like all harness tools, `web_search` is in the default set,
    so a skill with **no `tools:` allow-list can call it**. To keep a skill off the
    network, give it a `tools:` allow-list that omits `web_search` (and
    `run_command`). It's not implicitly sandboxed — the allow-list is the control.
- **`ask_user`** (`prompt`) pauses the workflow and reads one line the human types
  on the console (after Enter) — for confirmations, plan approval, or
  clarifications. It **blocks**, so it is an **opt-in tool: NOT in the default
  set** — a skill gets it only by naming `ask_user` in its `tools:` allow-list
  (so it can never fire in an autonomous flow that didn't ask for it). Returns a
  graceful `ERROR:` string (never blocks/aborts) when there's no interactive
  console: stdin isn't a TTY (backgrounded / piped / CI), stdin is absent, or the
  user hits Ctrl+C / EOF.
- **`run_command` is NOT sandboxed** (real shell, cwd = workspace) but is screened
  by a denylist policy (rm -rf, sudo, curl|sh, …); a refused command returns an
  `ERROR:` string instead of running. The venv is auto-activated once it exists.

## Conventions & gotchas

- **Deterministic vs LLM.** If a step has one correct mechanical action (run a
  script, commit, evaluate a metric), make it a `command`. Reserve `agent` steps
  for genuine content generation/judgment. Keeping scoring/IO deterministic is
  what makes runs reproducible.
- **Capture machine-readable signals**, not prose. Have a command print
  `SCORE=0.93` (or read a JSON file and print it) and `set:` a regex on it, rather
  than asking an agent to eyeball a number.
- **Helper scripts.** A step's `run_command` cwd is the **workspace**, while helper
  `.py` files live in the **flow dir**. Two reliable patterns:
  1. Call from a `command` step by templated path: `{{ python }} "{{ flow_dir }}/seed.py"`.
  2. If an **agent** must run a helper, **stage it into the workspace first** in a
     `command` step (`cp "{{ flow_dir }}/runsql.py" .`) and have the skill call it
     by relative path. (Do not put `{{ flow_dir }}` reasoning on the agent.)
- **Loop budget = compute budget.** There is no "give up after N failures" stop in
  `counting_loop`; it exits only on `exit_when` or `max_iterations`. Raise
  `max_iterations` to explore longer.
- **Feedback re-injection** is automatic in `retry_loop`: the `check` skill's full
  reply is appended to the next `action`'s task under a "Feedback from previous
  attempt" header — so write `check` feedback as actionable instructions.
- **Commands are POSIX sh on every OS.** On native Windows the engine runs
  `command:` steps and `run_command` through Git Bash (bundled with the
  already-required Git for Windows; `SAAGE_SHELL` overrides discovery), so
  quoting, `$VAR`, `&&`, and `>>` behave identically everywhere. Two
  portability rules: invoke helpers with `{{ python }}` (not a hardcoded
  `python3`), and avoid POSIX-absolute paths like `/tmp` inside commands —
  under Git Bash they resolve into the MSYS root, not `C:\tmp`; prefer
  workspace-relative paths or `{{ workspace }}`.
- **Resumable runs / restart-safe iterations.** `saage run` checkpoints after
  every step and loop iteration; `saage resume` restarts at the top-level step
  that was in progress. A killed loop iteration is redone *whole* from the body's
  first step, so write loop bodies to tolerate re-running the current iteration
  (e.g. clean the experiment dir at the top of the body before training, as the
  hill-climb flows do). Completed iterations are never redone. Resume granularity
  is the *outermost* loop's iteration: a loop nested inside another loop is **not**
  resumed independently — a crash redoes the whole in-progress outer iteration and
  re-runs the inner loop from scratch. It stays correct, but keep inner loops
  cheap (or avoid nesting) where resume cost matters.

## Running a flow

```bash
saage run flows/<name>/flow.yaml \
  [--workspace DIR] [--venv DIR] \
  [--provider openrouter --model "..."] [--base-url URL] \
  [--config engine.yaml] \              # tune the run_command safety policy
  [--set key=value ...]                 # seed/override shared values (JSON-parsed)
```

The engine logs each step as it runs and prints a run summary (steps, loop
outcomes, files written) at the end. `-v` for tool/output detail, `-q` to quiet.

## Recipe: add a new flow

1. `mkdir -p flows/<name>` and write `flow.yaml` (provider + shared + workflow).
2. For each `agent` step, create `flows/<name>/<skill>/skill.md` (frontmatter +
   body). Decide the `tools:` allow-list. Loop `check`/`status` skills must emit
   `ACTION:`.
3. Choose the primitive: generate-then-verify → `retry_loop`; wait on an external
   job → `polling_loop`; iterate/optimize → `counting_loop` (+ `exit_when`).
4. Wire data flow: `set:` to capture, `{{ }}` to consume, `exit_when` to stop.
5. Put any helper scripts in the flow dir; stage into the workspace if an agent
   runs them.

## Test your flow before a live run

**Hydrate-check** (no API calls — validates YAML + skill wiring; `python3` on
POSIX, `python` on Windows):
```bash
python -c "from saage.hydrate import build_flow; build_flow('flows/<name>/flow.yaml', provider=object(), workspace='_chk_ws'); print('ok')"
```

Then a **live** run against a real provider with a throwaway `--workspace`. The
repo's pytest suite (`pytest -q`) stays offline by scripting the LLM turns via the
`ScriptedProvider`/`RoutedProvider` test doubles — mirror an existing
`tests/integration` flow if you want an offline regression test for your flow.
