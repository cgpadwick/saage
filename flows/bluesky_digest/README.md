# bluesky_digest

Digests recent posts from the Bluesky accounts you care about (ML folks like
Sebastian Raschka and Simon Willison are active there), ranked against your
interests, with a verifier loop that rejects any digest citing posts that
aren't in the gathered notes. Unlike X, **Bluesky's API is free** — auth is
just an app password. Uses
[gwbischof/bluesky-social-mcp](https://github.com/gwbischof/bluesky-social-mcp)
as a local stdio server (version-pinned git tag).

## One-time setup

1. Create an app password (NOT your real password):
   https://bsky.app/settings/app-passwords
2. Store it plus your handle:

   ```bash
   saage mcp add bluesky
   ```

   Run from the repo root it discovers the var names (`BLUESKY_IDENTIFIER` —
   your handle, e.g. `you.bsky.social` — and `BLUESKY_APP_PASSWORD`) from this
   flow and prompts; exported env vars of the same names take precedence.
3. `uvx` on PATH (comes with [uv](https://docs.astral.sh/uv/)).

## Run

```bash
saage run flows/bluesky_digest/flow.yaml \
  --set handles="sebastianraschka.com, simonwillison.net" \
  --set interests="LLM research, local inference"
```

Output: `notes.md` (raw gathered posts) and `digest.md` (the ranked digest)
in the flow directory.

## How it works

`gather` (agent + `bluesky__get_author_feed` per handle) → `digest_loop`
retry_loop: `write_digest` → `verify_digest`, which fails the loop (with
feedback) if the digest cites anything not traceable to notes.md. Max 3
attempts.
