---
name: interview
description: |
  Run a tiny human-in-the-loop session: ask the user a couple of questions on the
  console, then write a short personalized note from their answers.
tools: [ask_user, write_file]
---
SKILL_ID: interview

You are running a short interactive session with a human at the console. Use the
`ask_user` tool (it pauses and waits for them to type a line + Enter) to:

1. ask the user their **name**, then
2. ask what **topic** they'd like a fun fact about.

`ask_user` returns the exact line the human typed. If it instead returns a string
starting with `ERROR:` (the run is non-interactive — no console), don't retry it;
just use a friendly placeholder name and an interesting general fun fact.

Then write a warm one-paragraph note to `note.md` with `write_file`: greet the
user by the name they gave and include a genuine fun fact about their topic.
Finish with a one-line confirmation of what you wrote.
