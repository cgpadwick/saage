#!/usr/bin/env python3
"""Validate the researcher's structured output and render the menu.

The researcher writes `autoresearch_ideas.json` (a documented schema —
never prose-parsed). This check, used as the inner retry_loop gate (E2):

  1. validates the STRUCTURE: parseable JSON, required fields present and
     non-empty, ranks unique/contiguous, >=6 ideas, >=4 distinct
     categories, no exact-duplicate titles or change texts;
  2. on pass, renders `autoresearch_ideas.md` from the JSON — the
     human/LLM-readable menu the proposer and critics read. Generated,
     never parsed.

Semantic quality (real diversity, groundedness, ranking sense) is the
research_critic agent's job, not this script's. cwd = workspace.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

IDEA_FIELDS = ("rank", "title", "category", "change", "why", "cost")
MIN_IDEAS, MIN_CATEGORIES = 6, 4


def issues_in(doc) -> list[str]:
    issues = []
    if not isinstance(doc, dict):
        return ["top level must be a JSON object"]
    if not doc.get("constraints") or not isinstance(doc["constraints"], list):
        issues.append("'constraints' must be a non-empty list of strings")
    ideas = doc.get("ideas")
    if not isinstance(ideas, list) or len(ideas) < MIN_IDEAS:
        issues.append(f"'ideas' must be a list of at least {MIN_IDEAS} "
                      f"(got {len(ideas) if isinstance(ideas, list) else 'none'})")
        ideas = ideas if isinstance(ideas, list) else []
    for i, idea in enumerate(ideas):
        if not isinstance(idea, dict):
            issues.append(f"ideas[{i}] is not an object")
            continue
        for f in IDEA_FIELDS:
            v = idea.get(f)
            if v is None or (isinstance(v, str) and not v.strip()):
                issues.append(f"ideas[{i}] missing/empty field {f!r}")
    dicts = [i for i in ideas if isinstance(i, dict)]
    ranks = [i.get("rank") for i in dicts]
    if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
        issues.append(f"ranks must be unique and contiguous from 1 (got {sorted(ranks)})")
    cats = {str(i.get("category", "")).strip().lower() for i in dicts} - {""}
    if len(cats) < MIN_CATEGORIES:
        issues.append(f"ideas span {len(cats)} distinct categories — "
                      f"need at least {MIN_CATEGORIES}")
    for field in ("title", "change"):
        seen: dict[str, int] = {}
        for i in dicts:
            key = " ".join(str(i.get(field, "")).lower().split())
            if key and key in seen:
                issues.append(f"ideas {seen[key]} and {i.get('rank')} have an "
                              f"identical {field} — exact duplicates are padding")
            seen[key] = i.get("rank")
    anti = doc.get("anti_ideas")
    if not isinstance(anti, list) or not anti:
        issues.append("'anti_ideas' must be a non-empty list")
    else:
        for i, a in enumerate(anti):
            if not isinstance(a, dict) or not str(a.get("technique", "")).strip() \
                    or not str(a.get("reason", "")).strip():
                issues.append(f"anti_ideas[{i}] needs non-empty 'technique' and 'reason'")
    return issues


def render(doc: dict) -> str:
    lines = ["# Autoresearch ideas (rendered from autoresearch_ideas.json)", "",
             "**Hard constraints (do not violate):**"]
    lines += [f"- {c}" for c in doc["constraints"]]
    lines += ["", "## Ranked ideas"]
    for i in sorted(doc["ideas"], key=lambda x: x["rank"]):
        lines += [f"### {i['rank']}. {i['title']} [{i['category']}]",
                  f"**Change:** {i['change']}",
                  f"**Why:** {i['why']}",
                  f"**Cost:** {i['cost']}", ""]
    lines += ["## Anti-ideas (do not propose)"]
    lines += [f"- {a['technique']} — {a['reason']}" for a in doc["anti_ideas"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = Path("autoresearch_ideas.json")
    if not p.exists():
        print("ISSUE: autoresearch_ideas.json does not exist in the workspace root")
        print("ACTION: fail")
        return
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ISSUE: autoresearch_ideas.json is not valid JSON: {exc}")
        print("ACTION: fail")
        return
    issues = issues_in(doc)
    if issues:
        for i in issues:
            print(f"ISSUE: {i}")
        print("ACTION: fail")
        return
    Path("autoresearch_ideas.md").write_text(render(doc), encoding="utf-8")
    print(f"menu ok: {len(doc['ideas'])} ideas, "
          f"{len({i['category'].lower() for i in doc['ideas']})} categories, "
          f"{len(doc['anti_ideas'])} anti-ideas — rendered autoresearch_ideas.md")
    print("ACTION: pass")


if __name__ == "__main__":
    sys.exit(main())
