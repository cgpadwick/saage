# tests/test_tagging.py
"""Every node must know which top-level workflow step it belongs to."""
from saage.hydrate import build_flow


# Mirrors _tag_step's traversal shape, kept separate so the test does not
# circularly depend on the helper it verifies.
def _all_nodes(node, seen=None, out=None):
    seen = set() if seen is None else seen
    out = [] if out is None else out
    if node is None or id(node) in seen:
        return out
    seen.add(id(node))
    out.append(node)
    start = getattr(node, "start_node", None)
    if start is not None:
        _all_nodes(start, seen, out)
    for nxt in getattr(node, "successors", {}).values():
        _all_nodes(nxt, seen, out)
    return out


def test_every_node_tagged_with_its_top_level_step(tmp_path):
    flow_yaml = tmp_path / "flow.yaml"
    flow_yaml.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - {id: first, type: command, run: 'echo a'}\n"
        "  - id: loop\n"
        "    type: counting_loop\n"
        "    max_iterations: 2\n"
        "    body:\n"
        "      - {id: tick, type: command, run: 'echo b'}\n"
        "  - {id: last, type: command, run: 'echo c'}\n"
    )
    flow, _ = build_flow(flow_yaml, provider=object(), workspace=str(tmp_path))
    # walk from the resume step list via the top flow's start chain
    nodes = _all_nodes(flow.start_node)
    # the loop body node 'tick' belongs to top-level step index 1 (the loop)
    tick = [n for n in nodes if getattr(n, "id", None) == "tick"][0]
    assert tick._step_index == 1
    first = [n for n in nodes if getattr(n, "id", None) == "first"][0]
    assert first._step_index == 0
    last = [n for n in nodes if getattr(n, "id", None) == "last"][0]
    assert last._step_index == 2
    # the loop Subflow node itself (not just its body) must be tagged too
    loop_node = flow.start_node.successors["default"]
    assert loop_node._step_index == 1


def test_retry_loop_internal_nodes_tagged(tmp_path):
    flow_yaml = tmp_path / "flow.yaml"
    flow_yaml.write_text(
        "provider: {type: local, model: x}\n"
        "workflow:\n"
        "  - {id: pre, type: command, run: 'echo a'}\n"
        "  - id: retry\n"
        "    type: retry_loop\n"
        "    max_iterations: 2\n"
        "    action: {id: act, type: command, run: 'echo b'}\n"
        "    check:  {id: chk, type: command, run: 'echo ACTION: pass'}\n"
    )
    flow, _ = build_flow(flow_yaml, provider=object(), workspace=str(tmp_path))
    nodes = _all_nodes(flow.start_node)
    by_id = {getattr(n, "id", None): n for n in nodes}
    assert by_id["pre"]._step_index == 0
    assert by_id["act"]._step_index == 1     # retry_loop body -> step 1
    assert by_id["chk"]._step_index == 1
