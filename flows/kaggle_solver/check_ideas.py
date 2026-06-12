#!/usr/bin/env python3
"""Deterministic check for the researcher's menu (retry_loop check, E2):
autoresearch_ideas.md must exist with a ranked list (>=6 ideas), category
tags, and an anti-ideas section. Prints issues as feedback for the retry.
cwd = workspace."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
    issues = []
    p = Path("autoresearch_ideas.md")
    if not p.exists():
        issues.append("autoresearch_ideas.md does not exist in the workspace root")
    else:
        text = p.read_text(encoding="utf-8")
        ideas = re.findall(r"^### +\d+\.", text, flags=re.M)
        if len(ideas) < 6:
            issues.append(f"only {len(ideas)} ranked '### <n>.' ideas — need at least 6")
        cats = set(re.findall(r"^### +\d+\..*\[([^\]]+)\]", text, flags=re.M))
        if len(cats) < 4:
            issues.append(f"ideas span only {len(cats)} bracketed [category] tags — "
                          "need at least 4 distinct categories")
        if not re.search(r"^## +Anti-ideas", text, flags=re.M):
            issues.append("missing the '## Anti-ideas' section")
        if "constraint" not in text.lower():
            issues.append("missing the hard-constraints block")
    if issues:
        for i in issues:
            print(f"ISSUE: {i}")
        print("ACTION: fail")
        return
    print(f"menu ok: {len(ideas)} ideas, {len(cats)} categories")
    print("ACTION: pass")


if __name__ == "__main__":
    sys.exit(main())
