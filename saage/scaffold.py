"""`saage new <name>` — scaffold a runnable flow directory from a template."""
from __future__ import annotations

import shutil
from pathlib import Path

_FLOW_YAML = '''\
# {name}: describe what this flow does. This first comment line is the
# description shown in the `saage serve` UI.
provider: {{ type: openrouter, model: "openai/gpt-4o-mini" }}

# Knobs: every key here can be overridden at run time with --set key=value
# (and shows up as a form field in the web UI).
shared:
  topic: "example"

workflow:
  - id: work
    type: agent
    skill: do_work
  - id: record
    type: command
    run: "echo finished: {{{{ topic }}}} > result.txt"
'''

_SKILL_MD = '''\
---
description: Do the work for {{ topic }} and write the result to output.md.
tools: [read_file, write_file]
---
SKILL_ID: do_work

Replace these instructions with what the agent should actually do.

- The description above is the task message; this body is the system prompt.
- `{{ topic }}` placeholders are filled from the shared store before the
  model sees the text.
- Finish by writing your result to `output.md` with the write_file tool.
'''


def new_flow(name: str, parent: Path | None = None) -> Path:
    """Create <parent>/<name> with a template flow.yaml + one skill.

    Default parent: ./flows when it exists (the repo convention), else the
    current directory. Refuses to touch an existing directory.
    """
    if name != Path(name).name or name.startswith("."):
        # one plain directory name — no separators, no '..', no hidden dirs
        raise ValueError(f"invalid flow name {name!r}: use a plain directory "
                         f"name (letters, digits, _ or -)")
    if parent is None:
        parent = Path("flows") if Path("flows").is_dir() else Path(".")
    dest = parent / name
    if dest.exists():
        raise FileExistsError(f"{dest} already exists — pick another name or delete it")
    (dest / "do_work").mkdir(parents=True)
    try:
        (dest / "flow.yaml").write_text(_FLOW_YAML.format(name=name), encoding="utf-8")
        (dest / "do_work" / "skill.md").write_text(_SKILL_MD, encoding="utf-8")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)   # no half-written scaffold
        raise
    return dest
