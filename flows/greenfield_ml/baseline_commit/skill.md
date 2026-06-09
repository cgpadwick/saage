---
name: baseline_commit
description: |
  Commit the baseline solution so the hill-climb has a base to revert to.
  Stage everything in the workspace and commit it with EXACTLY this message:
  `baseline score {{ best_score }}`
  If there is nothing to commit, that is fine. Finish with a one-line confirmation.
tools: [run_command, git_status, git_add, git_commit]
---
SKILL_ID: baseline_commit

You commit the baseline solution. Stage all changes and commit with the exact
message given in the task. Do not change any code.
