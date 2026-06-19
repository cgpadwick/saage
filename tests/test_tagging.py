# tests/test_tagging.py
"""Every node must know which top-level workflow step it belongs to."""
from saage.hydrate import build_flow


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
