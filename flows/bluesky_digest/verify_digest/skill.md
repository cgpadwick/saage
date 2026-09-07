---
name: verify_digest
description: "Check that digest.md only cites posts that really appear in notes.md."
tools: [read_file]
---
SKILL_ID: verify_digest

Read `notes.md` and `digest.md`. The digest passes only if every post it
mentions — author, quoted/paraphrased content, and URI — is traceable to an
entry in notes.md (no invented posts, no misattributed authors, no altered
links) and every item has a stated reason for inclusion.

End your reply with exactly one line:
`ACTION: pass` if the digest checks out, or
`ACTION: fail` after listing each problem (the list is fed back to the
writer). No ACTION line counts as fail.
