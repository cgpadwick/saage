# reddit_digest

Digests the current top posts of the subreddits you choose, ranked against
your interests, with a verifier loop that rejects any digest citing posts
that aren't really there. First demo of saage's MCP client: the flow's
`mcp:` block spawns [GridfireAI/reddit-mcp](https://github.com/GridfireAI/reddit-mcp)
(read-only, PRAW-based) as a local stdio server.

## One-time setup

1. Create a Reddit **script app** (free): https://www.reddit.com/prefs/apps →
   "create app" → type *script*. That yields a `client_id` (under the app
   name) and a `client_secret`.
2. Store them for this server:

   ```bash
   saage mcp add reddit
   ```

   Run from the repo root it discovers the var names (REDDIT_CLIENT_ID,
   REDDIT_CLIENT_SECRET) from this flow's `mcp:` block and prompts for each;
   you can also name them explicitly. Exported env vars of the same names
   take precedence.
3. `uvx` must be on PATH (comes with [uv](https://docs.astral.sh/uv/)) — the
   flow launches the server with `uvx reddit-mcp`.

## Run

```bash
saage run flows/reddit_digest/flow.yaml \
  --set subreddits="LocalLLaMA, rust" \
  --set interests="local inference, systems programming"
```

Output: `notes.md` (raw gathered posts) and `digest.md` (the ranked digest)
in the flow directory.

## How it works

`gather` (agent + MCP tools) → `digest_loop` retry_loop:
`write_digest` → `verify_digest`, which fails the loop (with feedback) if the
digest cites a title or URL not present verbatim in notes.md. Max 3 attempts.
