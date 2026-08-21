---
name: building-saage-flows
description: Use when asked to create, build, author, or modify a saage flow or workflow (flow.yaml + skill directories) from a user request or plain-English description — including adding steps, loops, or skills to an existing flow.
---

# Building saage flows

**Any coding agent can use this file** — it is plain markdown with no
Claude-specific steps. It lives under `.claude/skills/` so harnesses with skill
support auto-discover it; everyone else gets here via the pointer at the top of
`AGENTS.md`.

A flow is a directory: `flows/<name>/flow.yaml` plus one subdirectory per agent skill (`<skill_dir>/skill.md`). `AGENTS.md` at the repo root is the full schema reference — read its schema section when you need field-level detail. This skill is the build loop plus the facts AGENTS.md gets wrong or omits.

## The loop

1. **Pick the primitive** (below), author `flows/<name>/flow.yaml` + skill dirs.
2. **Hydrate-check** — free, no API key, catches schema/wiring/skill errors:
   ```bash
   python -c "from saage.hydrate import build_flow; import tempfile; build_flow('flows/<name>/flow.yaml', provider=object(), workspace=tempfile.mkdtemp()); print('ok')"
   ```
   (`tests/test_flows_hydrate.py` auto-covers every `flows/*/flow.yaml` — your new flow is in the suite the moment the directory exists.)
3. **Write an offline integration test** — `tests/integration/test_<name>.py` using `RoutedProvider` (recipe below). Real engine, real files/commands; only LLM turns are scripted.
4. **`pytest tests/integration/test_<name>.py -q`** — iterate until green.
5. Only then offer a live run: `saage run flows/<name>/flow.yaml` (needs the provider's API key env var).

Do not skip 2–4: a flow that only "looks right" routinely fails on ACTION routing or capture regexes, and the offline test costs zero tokens.

## Choosing the primitive

| User's shape | Primitive |
|---|---|
| do → judge → redo-with-feedback until accepted | `retry_loop` (action + check; checker's reply is auto-fed back on `fail`) |
| submit → wait → poll status until done | `polling_loop` (poll + status; hard `max_wait_seconds` cap) |
| iterate N times / until a metric hits a target | `counting_loop` (body + `max_iterations` and/or `exit_when`) |
| one-shot LLM work | plain `agent` step |
| deterministic shell (tests, training, data prep) | `command` step — never make an agent run what a command can |

## Traps — facts AGENTS.md omits or gets wrong

- **`SKILL_ID` is mandatory in practice.** First body line of every skill.md: `SKILL_ID: <label>`, unique per flow. AGENTS.md calls it optional, but `RoutedProvider` routes scripted turns by regexing it from the system prompt — without it no offline test can exist.
- **Result shapes differ:** `results['<id>']` for an *agent* step is a plain string (final text); for a *command* step it's `{exit, stdout, stderr}`. `{{ results['x']['stdout'] }}` on an agent step is wrong.
- **Only `counting_loop.max_iterations` is templatable** (int, numeric string, or `"{{ var | default(12) }}"` — enables `--set` override). `retry_loop`/`polling_loop` bounds are raw ints only.
- **Check/status steps must end with a literal `ACTION: <word>` line.** No `ACTION:` ≠ success — it routes `default`, i.e. retry / keep polling. Say this explicitly in the skill body.
- **`set: {var: regex}` leaves the var unchanged on no-match.** Pre-seed sentinel values in `shared:` (e.g. `accuracy: nan`) so `exit_when` never hits an undefined name. Capture uses group 1 if present else whole match, last match wins, coerces int/float.
- **Flow description in the web UI = the first `#` comment line of flow.yaml; knobs = the `shared:` block.** Always start flow.yaml with a `#` comment describing the flow, and expose every tunable as a `shared:` key.
- **Skills are referenced by directory name**, not frontmatter `name:`. `tools:` frontmatter is an allow-list (`tools: []` = no tools; omit = all); all-unknown tool names raise at build.
- Engine-owned shared keys to assert on in tests: `_trace` (step ids in order), `_iter[<loop_id>]`, `_exit_reason[<loop_id>]` (`"exit_when"` or `"max_iterations"`), `_feedback`, `results`.
- Undefined `{{ var }}` renders `""` with a warning — pre-seed everything you template.

## Offline test recipe

Canonical examples: `tests/integration/test_guessing_game.py` (loop + exit_when + real command), `test_story_writer.py` (determinism), `test_fix_failing_test.py` (real file edits + real pytest). Helpers in `tests/saage_testkit.py`, fixtures in `tests/conftest.py`.

```python
from saage_testkit import RoutedProvider, resp, tool_turn   # conftest puts tests/ on sys.path
from saage.hydrate import run_flow                          # run_flow(flow_yaml, provider=..., shared=...) — knob overrides go in `shared=`, not "overrides"

def test_haiku_flow(flow_copy):                      # flow_copy fixture: hermetic copy of flows/<name>
    provider = RoutedProvider({
        "write_haiku": tool_turn("write_file", path="haiku.md", content="...")
                     + tool_turn("write_file", path="haiku.md", content="...v2"),
        "critique_haiku": [resp("Too abstract.\nACTION: fail"), resp("ACTION: pass")],
    })
    shared = run_flow(flow_copy("haiku_writer"), provider=provider)
    assert shared["_trace"] == ["write", "critique", "write", "critique"]
```

Keys in the `RoutedProvider` dict = the `SKILL_ID` labels; each maps to a flat queue of `LLMResponse` turns, exhaustion raises. `resp(text)` = one plain text turn; `tool_turn(name, **args)` **returns a two-turn list** (tool call, then "done") — concatenate with `+`, never nest lists.

## Conventions checklist

- flow.yaml starts with a `#` description comment; knobs in `shared:` with sane defaults.
- Provider: copy an existing flow's `provider:` block; note the user can override with `--provider/--model` at run time.
- Skill bodies: terse, imperative; heavy per-task instructions can live in the templated `description:` instead (see `flows/guessing_game/judge/skill.md`).
- Loop bodies must be safe to re-run (resume redoes the in-progress iteration).
- Add nothing to `tests/conftest.py` unless your flow writes artifacts into its own dir (then extend the `flow_copy` ignore list).
