"""bluesky_digest end to end with the fake bluesky MCP server (real stdio
client, scripted LLM turns), including the verify-fail → feedback path."""
import sys
from pathlib import Path

import yaml

from saage.hydrate import run_flow
from saage_testkit import RoutedProvider, call, resp, tool_turn

FAKE = str(Path(__file__).resolve().parent.parent / "fake_mcp_server.py")


def _point_at_fake(flow_yaml: Path, server: str, profile: str) -> None:
    spec = yaml.safe_load(flow_yaml.read_text(encoding="utf-8"))
    spec["mcp"][server] = {"command": sys.executable,
                           "args": [FAKE, "--profile", profile]}
    flow_yaml.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


class RecordingProvider(RoutedProvider):
    def __init__(self, scripts):
        super().__init__(scripts)
        self.tool_results: list[str] = []

    def complete(self, system, messages, tools):
        for m in messages:
            if m.get("role") == "tool":
                self.tool_results += [out for _, out in m["results"]]
        return super().complete(system, messages, tools)


NOTES = """# sebastianraschka.com
- "New post: LoRA vs full fine-tuning, benchmarked" (2026-08-29, 412 likes)
  at://did:plc:rasbt/app.bsky.feed.post/1
# simonwillison.net
- "TIL sqlite-utils can do this" (2026-08-29, 233 likes)
  at://did:plc:simonw/app.bsky.feed.post/9
"""

BAD_DIGEST = ("# Digest\n- sebastianraschka.com announced a new book "
              "(at://made.up/post) — big news\n")

GOOD_DIGEST = ("# Digest\n- sebastianraschka.com: \"New post: LoRA vs full "
               "fine-tuning, benchmarked\" (at://did:plc:rasbt/"
               "app.bsky.feed.post/1) — directly about LLM research\n")


def test_bluesky_digest_flow(flow_copy):
    provider = RecordingProvider({
        "gather": [
            resp(calls=[call("bluesky__get_author_feed",
                             actor="sebastianraschka.com", limit=20)]),
            resp(calls=[call("bluesky__get_author_feed",
                             actor="simonwillison.net", limit=20)]),
            resp(calls=[call("write_file", path="notes.md", content=NOTES)]),
            resp("done"),
        ],
        "write_digest":
            tool_turn("write_file", path="digest.md", content=BAD_DIGEST)
            + tool_turn("write_file", path="digest.md", content=GOOD_DIGEST),
        "verify_digest": [
            resp("digest cites a post not present in notes.md\nACTION: fail"),
            resp("ACTION: pass"),
        ],
    })
    flow_yaml = flow_copy("bluesky_digest")
    _point_at_fake(flow_yaml, "bluesky", "bluesky")
    shared = run_flow(flow_yaml, provider=provider)

    assert shared["_trace"] == ["gather", "write_digest", "verify_digest",
                                "write_digest", "verify_digest"]
    ws = flow_yaml.parent
    assert (ws / "notes.md").read_text(encoding="utf-8") == NOTES
    assert (ws / "digest.md").read_text(encoding="utf-8") == GOOD_DIGEST
    # both author feeds really came back through the stdio client
    assert any("LoRA vs full fine-tuning" in r for r in provider.tool_results)
    assert any("sqlite-utils" in r for r in provider.tool_results)
    assert not any(r.startswith("ERROR: unknown tool")
                   for r in provider.tool_results)
