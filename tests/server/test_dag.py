"""DAG builder, ledger state reducer, and SVG renderer for saage server."""
import yaml

from saage.server.dag import build_graph, reduce_states, render_svg

FLOW = yaml.safe_load("""
provider: {type: local, model: m}
workflow:
  - {id: a, type: command, run: 'echo hi'}
  - id: fixit
    type: retry_loop
    max_iterations: 3
    action: {id: fix, type: agent, skill: s1}
    check: {id: verify, type: command, run: 'pytest -q'}
  - {id: z, type: command, run: 'echo done'}
""")


def test_graph_walks_loops_into_clusters():
    g = build_graph(FLOW)
    ids = [n.id for n in g.nodes]
    assert ids == ["a", "fix", "verify", "z"]
    assert ("a", "fix") in g.edges and ("verify", "z") in g.edges
    assert ("fix", "verify") in g.edges
    cl = next(c for c in g.clusters if c.id == "fixit")
    assert cl.kind == "retry_loop" and cl.max_iterations == "3"
    assert next(n for n in g.nodes if n.id == "fix").cluster == "fixit"


def test_reducer_tracks_running_done_failed_attempts():
    ev = [{"node": "a", "phase": "start"},
          {"node": "a", "phase": "end", "action": "default", "exit": 0},
          {"node": "fix", "phase": "start"},
          {"node": "fix", "phase": "end", "action": "default"},
          {"node": "verify", "phase": "start"},
          {"node": "verify", "phase": "end", "action": "fail", "exit": 1},
          {"node": "fix", "phase": "start"}]
    s = reduce_states(ev)
    assert s["a"]["state"] == "done"
    assert s["verify"]["state"] == "failed"
    assert s["fix"]["state"] == "running" and s["fix"]["attempts"] == 2


def test_svg_marks_states():
    g = build_graph(FLOW)
    svg = render_svg(g, {"a": {"state": "done", "attempts": 1, "last": {}}})
    assert svg.startswith("<svg") and 'id="node-a"' in svg and "state-done" in svg
    assert "state-pending" in svg          # nodes without events default pending
