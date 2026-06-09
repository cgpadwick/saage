---
name: git_init
description: |
  Initialize git version control in the workspace (if it isn't already a repo) so
  the hill-climb can commit improvements and revert regressions.
tools: [run_command, write_file, git_status, git_add, git_commit]
---
SKILL_ID: git_init

You set up git for the workspace. Commands run at the workspace root.

1. Check whether the workspace is already a git repo: `git rev-parse --git-dir`.
   If it already is one, do nothing and finish.
2. Otherwise, write a `.gitignore` excluding build/data/env artifacts AND the
   hill-climb's own bookkeeping (which must survive `git clean` between experiments):
   `.venv/`, `data/`, `__pycache__/`, `*.pyc`, `logs/`, `checkpoints/`, `*.pt`, `*.pth`,
   `proposals/`, `experiments.jsonl`, `report.html`, `report_assets/`.
3. Run `git init`, then set a local commit identity so commits don't fail on a
   box without global git config:
   `git config user.email saage@local` and `git config user.name saage`.
4. Stage everything and make the initial commit with message `init workspace`.
5. Finish with a one-line confirmation.
