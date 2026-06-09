"""Skills are directories containing a `skill.md` (Claude-style frontmatter + body).

Optional `.py` files may sit beside `skill.md`; the skill's instructions tell the
agent to run them via the `run_command` tool. There is intentionally no special
code loader — that keeps the engine small.

Both the frontmatter `description:` and the markdown body are Jinja-templated from
the shared store when the step runs, so instructions can reference run values
directly (e.g. ``Answer this question: {{ question }}``). An undefined name
renders to "" and logs a warning. To keep a literal brace from being interpreted,
wrap it in ``{% raw %}…{% endraw %}``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    system: str               # the markdown body — the agent's instructions
    dir: Path
    tools: list[str] | None   # optional allow-list of tool names


def parse_skill(md: Path) -> Skill:
    md = Path(md)
    text = md.read_text()
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) != 3:
            # opened a frontmatter block but never closed it with a matching '---'
            log.warning("skill %s: opens with '---' but the YAML frontmatter is not "
                        "closed with a matching '---'; treating the whole file as the "
                        "instruction body (name/description/tools will be defaults).", md)
        else:
            _, fm, body = parts
            try:
                loaded = yaml.safe_load(fm)
            except yaml.YAMLError as e:
                log.warning("skill %s: malformed YAML frontmatter (%s); ignoring it.", md, e)
                loaded = None
            if loaded is None:
                meta = {}                       # empty frontmatter is fine (defaults)
            elif isinstance(loaded, dict):
                meta = loaded
            else:
                log.warning("skill %s: frontmatter is %s, expected a mapping "
                            "(name:/description:/tools:); ignoring it.",
                            md, type(loaded).__name__)
    return Skill(
        name=meta.get("name", md.parent.name),
        description=meta.get("description", ""),
        system=body.strip(),
        dir=md.parent,
        tools=meta.get("tools"),
    )


def load_skills(flow_dir: Path) -> dict[str, Skill]:
    flow_dir = Path(flow_dir)
    skills: dict[str, Skill] = {}
    for d in sorted(flow_dir.iterdir()):
        md = d / "skill.md"
        if md.is_file():
            skills[d.name] = parse_skill(md)
    return skills
