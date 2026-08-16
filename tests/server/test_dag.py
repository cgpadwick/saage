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

COUNTING_FLOW = yaml.safe_load("""
provider: {type: local, model: m}
workflow:
  - {id: start, type: command, run: 'echo start'}
  - id: loop
    type: counting_loop
    max_iterations: 5
    body:
      - {id: step1, type: command, run: 'echo step1'}
      - {id: step2, type: command, run: 'echo step2'}
  - {id: end, type: command, run: 'echo end'}
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


def test_counting_loop_body_list():
    """Test that counting_loop body (a list) is handled correctly."""
    g = build_graph(COUNTING_FLOW)
    ids = [n.id for n in g.nodes]
    assert ids == ["start", "step1", "step2", "end"]
    assert ("start", "step1") in g.edges
    assert ("step1", "step2") in g.edges
    assert ("step2", "end") in g.edges
    cl = next(c for c in g.clusters if c.id == "loop")
    assert cl.kind == "counting_loop" and cl.max_iterations == "5"
    assert next(n for n in g.nodes if n.id == "step1").cluster == "loop"
    assert next(n for n in g.nodes if n.id == "step2").cluster == "loop"


def test_retry_loop_back_edge():
    """Test that retry_loop draws back-edge from check to action."""
    g = build_graph(FLOW)
    # Should have forward edges: a->fix, fix->verify, verify->z
    assert ("a", "fix") in g.edges
    assert ("fix", "verify") in g.edges
    assert ("verify", "z") in g.edges
    # Should have back-edge: verify->fix (marking end of loop back to action)
    assert ("verify", "fix") in g.back_edges
    
    # SVG should render back-edge with dashed style
    svg = render_svg(g, {})
    # Check for back-edge path with backedge class
    assert 'class="backedge"' in svg


POLLING_FLOW = yaml.safe_load("""
provider: {type: local, model: m}
workflow:
  - {id: submit, type: command, run: 'echo go'}
  - id: wait
    type: polling_loop
    interval_seconds: 0
    max_wait_seconds: 60
    poll:   {id: poll,     type: command, run: 'echo poll'}
    status: {id: classify, type: agent,   skill: classify_job}
""")

NESTED_FLOW = yaml.safe_load("""
provider: {type: local, model: m}
workflow:
  - id: outer
    type: counting_loop
    max_iterations: 3
    body:
      - {id: prep, type: command, run: 'echo prep'}
      - id: inner
        type: retry_loop
        max_iterations: 2
        action: {id: fix, type: agent, skill: s1}
        check:  {id: verify, type: command, run: 'pytest -q'}
      - {id: wrap, type: command, run: 'echo wrap'}
""")


def test_polling_loop_uses_status_key():
    """The engine's polling_loop spec key is 'status' (see hydrate.build_step),
    so the classifier node must appear in the graph with a classify→poll back-edge."""
    g = build_graph(POLLING_FLOW)
    ids = [n.id for n in g.nodes]
    assert ids == ["submit", "poll", "classify"]
    assert ("poll", "classify") in g.edges
    assert ("classify", "poll") in g.back_edges
    assert ("poll", "poll") not in g.back_edges


def test_nested_loops_are_recursed():
    """A loop inside a loop's body must contribute its leaf nodes (not appear
    as an opaque leaf), with edges chained through the nesting."""
    g = build_graph(NESTED_FLOW)
    ids = [n.id for n in g.nodes]
    assert ids == ["prep", "fix", "verify", "wrap"]
    assert ("prep", "fix") in g.edges and ("verify", "wrap") in g.edges
    inner = next(c for c in g.clusters if c.id == "inner")
    outer = next(c for c in g.clusters if c.id == "outer")
    assert inner.parent == "outer" and inner.depth == 1
    assert outer.parent is None and outer.depth == 0
    assert ("verify", "fix") in g.back_edges
    # inner-loop leaves belong to the inner cluster
    assert next(n for n in g.nodes if n.id == "fix").cluster == "inner"
    # SVG renders without KeyError and wraps both clusters
    svg = render_svg(g, {})
    assert svg.count('class="cluster"') == 2


def test_reducer_treats_failed_action_as_failed():
    """Polling classifiers and terminal nodes emit action 'failed' (not 'fail')."""
    ev = [{"node": "classify", "phase": "start"},
          {"node": "classify", "phase": "end", "action": "failed"}]
    s = reduce_states(ev)
    assert s["classify"]["state"] == "failed"


def test_svg_escapes_flow_authored_markup():
    """Node ids/labels come from flow YAML and land in innerHTML — a crafted
    id must not survive as live markup."""
    evil = yaml.safe_load("""
provider: {type: local, model: m}
workflow:
  - {id: 'x"><img src=x onerror=alert(1)>', type: command, run: 'echo hi'}
""")
    svg = render_svg(build_graph(evil), {})
    assert "<img" not in svg           # never a live tag
    assert "&lt;img" in svg            # present only as escaped text
    # And the document must still be well-formed XML with no injected elements
    import xml.etree.ElementTree as ET
    root = ET.fromstring(svg)
    assert not [el for el in root.iter() if el.tag.endswith("img")]


def test_svg_nodes_are_keyboard_focusable():
    svg = render_svg(build_graph(FLOW), {})
    assert 'tabindex="0"' in svg and 'role="button"' in svg
