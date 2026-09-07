"""research_report end to end with the fake search MCP server (real stdio
client, scripted LLM turns): plan → search/extract → cited report → verify."""
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


SOURCES = """## [1] Result A for solid state batteries
https://example.com/a
- the answer is 42
"""

REPORT = """# Report
Solid-state progress is measurable [1].

## Sources
1. https://example.com/a
"""


def test_research_report_flow(flow_copy):
    provider = RecordingProvider({
        "plan": tool_turn("write_file", path="queries.md",
                          content="solid state batteries status\n"),
        "research": [
            resp(calls=[call("tavily__tavily-search",
                             query="solid state batteries status",
                             max_results=5)]),
            resp(calls=[call("tavily__tavily-extract",
                             urls="https://example.com/a")]),
            resp(calls=[call("write_file", path="sources.md",
                             content=SOURCES)]),
            resp("done"),
        ],
        "write_report": tool_turn("write_file", path="report.md",
                                  content=REPORT),
        "verify_report": [resp("ACTION: pass")],
    })
    flow_yaml = flow_copy("research_report")
    _point_at_fake(flow_yaml, "tavily", "search")
    shared = run_flow(flow_yaml, provider=provider)

    assert shared["_trace"] == ["plan", "research", "write_report",
                                "verify_report"]
    ws = flow_yaml.parent
    assert (ws / "queries.md").is_file()
    assert (ws / "sources.md").read_text(encoding="utf-8") == SOURCES
    assert (ws / "report.md").read_text(encoding="utf-8") == REPORT
    # both hyphen-named MCP tools really executed over stdio
    assert any("Result A for solid state batteries" in r
               for r in provider.tool_results)
    assert any("the answer is 42" in r for r in provider.tool_results)
    assert not any(r.startswith("ERROR: unknown tool")
                   for r in provider.tool_results)
