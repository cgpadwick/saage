---
name: gather
description: "Collect the current top posts from each of these subreddits: {{ subreddits }} ({{ posts_per_subreddit }} posts each), and save structured notes."
tools: [reddit__search_posts, reddit__get_comments_by_submission, write_file]
---
SKILL_ID: gather

You are collecting raw material for a digest. For EACH subreddit listed in the
task (comma-separated), call `reddit__search_posts` with that subreddit's name
and the requested limit. For the most interesting one or two posts you may
also pull comments with `reddit__get_comments_by_submission`.

Then write `notes.md`: one section per subreddit, one bullet per post with its
exact title, score, and URL (copied verbatim from the tool results — never
invent or reword them). Notable comment takeaways go under the post's bullet.

A tool reply starting with `ERROR:` means that subreddit failed — note it in
notes.md and continue with the rest.
