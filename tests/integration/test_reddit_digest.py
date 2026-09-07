"""reddit_digest end to end: real engine, real MCP client talking to the fake
reddit server (tests/fake_mcp_server.py) over actual stdio; only the LLM turns
are scripted. Covers the verify-fail → feedback → rewrite path."""
import sys
from pathlib import Path

import yaml

from saage.hydrate import run_flow
from saage_testkit import RoutedProvider, call, resp, tool_turn

FAKE = str(Path(__file__).resolve().parent.parent / "fake_mcp_server.py")


def _point_at_fake(flow_yaml: Path, server: str, profile: str) -> None:
    """Swap the flow's real MCP server for the offline fake (keeps the rest)."""
    spec = yaml.safe_load(flow_yaml.read_text(encoding="utf-8"))
    spec["mcp"][server] = {"command": sys.executable,
                           "args": [FAKE, "--profile", profile]}
    flow_yaml.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


class RecordingProvider(RoutedProvider):
    """Also keeps every tool result the model saw, so tests can prove the MCP
    round trip really happened (an unknown tool would yield an ERROR string)."""

    def __init__(self, scripts):
        super().__init__(scripts)
        self.tool_results: list[str] = []

    def complete(self, system, messages, tools):
        for m in messages:
            if m.get("role") == "tool":
                self.tool_results += [out for _, out in m["results"]]
        return super().complete(system, messages, tools)


NOTES = """# r/MachineLearning
- [New SOTA on ImageNet with 10 lines](https://reddit.com/r/MachineLearning/ml1) score 812
- [Why my transformer won't converge](https://reddit.com/r/MachineLearning/ml2) score 341
"""

BAD_DIGEST = ("# Digest\n- [Totally invented post](https://nowhere.example) — "
              "sounds relevant\n")

GOOD_DIGEST = ("# Digest\n- [New SOTA on ImageNet with 10 lines]"
               "(https://reddit.com/r/MachineLearning/ml1) — directly about "
               "ML research\n")


def test_reddit_digest_flow(flow_copy):
    provider = RecordingProvider({
        "gather": [
            resp(calls=[call("reddit__search_posts",
                             subreddit_name="MachineLearning",
                             query="*", limit=5)]),
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
    flow_yaml = flow_copy("reddit_digest")
    _point_at_fake(flow_yaml, "reddit", "reddit")
    shared = run_flow(flow_yaml, provider=provider)

    assert shared["_trace"] == ["gather", "write_digest", "verify_digest",
                                "write_digest", "verify_digest"]
    ws = flow_yaml.parent
    assert (ws / "notes.md").read_text(encoding="utf-8") == NOTES
    assert (ws / "digest.md").read_text(encoding="utf-8") == GOOD_DIGEST
    # the MCP round trip really happened: the fake server's canned post came
    # back through the real stdio client as a tool result
    assert any("New SOTA on ImageNet" in r for r in provider.tool_results)
    assert not any(r.startswith("ERROR: unknown tool")
                   for r in provider.tool_results)
