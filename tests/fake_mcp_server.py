"""A deterministic stdio MCP server for the offline test suite.

Run as `python -m tests.fake_mcp_server --profile echo|reddit|search`.
Each profile mimics the tool names of the real server a demo flow declares
(GridfireAI reddit-mcp, tavily-mcp) with canned, network-free responses, so
integration tests exercise the REAL client/loop/tool plumbing end to end.
"""
from __future__ import annotations

import argparse
import json
import os


def build(profile: str):
    from mcp.server.mcpserver import MCPServer
    server = MCPServer(name=f"fake-{profile}")

    if profile == "echo":
        @server.tool()
        def echo(text: str) -> str:
            """Echo the input back."""
            return f"echo: {text}"

        @server.tool()
        def env_probe(name: str) -> str:
            """Report an env var as seen by the server process."""
            return os.environ.get(name, "(unset)")

        @server.tool()
        def boom() -> str:
            """Always raises."""
            raise RuntimeError("kaboom")

    elif profile == "reddit":
        POSTS = {
            "MachineLearning": [
                {"id": "ml1", "title": "New SOTA on ImageNet with 10 lines",
                 "score": 812, "url": "https://reddit.com/r/MachineLearning/ml1"},
                {"id": "ml2", "title": "Why my transformer won't converge",
                 "score": 341, "url": "https://reddit.com/r/MachineLearning/ml2"},
            ],
            "Python": [
                {"id": "py1", "title": "PEP 9999: braces at last",
                 "score": 1523, "url": "https://reddit.com/r/Python/py1"},
            ],
        }

        @server.tool()
        def search_posts(subreddit_name: str, query: str = "*",
                         sort: str = "hot", limit: int = 10) -> str:
            """Search posts in a subreddit."""
            return json.dumps(POSTS.get(subreddit_name, [])[:limit])

        @server.tool()
        def get_subreddit(subreddit_name: str) -> str:
            """Access a subreddit by name."""
            return json.dumps({"name": subreddit_name,
                               "subscribers": 1000,
                               "description": f"all about {subreddit_name}"})

        @server.tool()
        def get_comments_by_submission(submission_id: str) -> str:
            """Access comments of a submission."""
            return json.dumps([
                {"author": "u/alice", "body": f"great point about {submission_id}"},
                {"author": "u/bob", "body": "disagree, see the docs"},
            ])

    elif profile == "search":
        @server.tool(name="tavily-search")
        def tavily_search(query: str, max_results: int = 5) -> str:
            """Web search."""
            return json.dumps([
                {"title": f"Result A for {query}",
                 "url": "https://example.com/a", "snippet": "Alpha facts."},
                {"title": f"Result B for {query}",
                 "url": "https://example.com/b", "snippet": "Beta facts."},
            ][:max_results])

        @server.tool(name="tavily-extract")
        def tavily_extract(urls: str) -> str:
            """Extract page content."""
            return json.dumps({"url": urls,
                               "content": f"Full text of {urls}: the answer is 42."})

    else:
        raise SystemExit(f"unknown profile: {profile}")
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="echo")
    build(ap.parse_args().profile).run("stdio")
