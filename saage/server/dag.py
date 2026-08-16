"""DAG builder, ledger state reducer, and SVG renderer for saage server."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape, quoteattr


@dataclass
class GNode:
    """A node in the DAG."""
    id: str
    type: str
    label: str
    params: dict = field(default_factory=dict)
    cluster: Optional[str] = None


@dataclass
class Cluster:
    """A cluster (loop) containing nodes."""
    id: str
    kind: str  # retry_loop, counting_loop, polling_loop
    label: str = ""
    max_iterations: str = ""
    depth: int = 0  # nesting depth (0 = top-level), used to inset nested rects
    parent: Optional[str] = None  # enclosing cluster id, for nested loops


@dataclass
class Graph:
    """A directed acyclic graph of nodes and clusters."""
    nodes: list[GNode] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)
    back_edges: list[tuple[str, str]] = field(default_factory=list)


# Which spec keys hold a loop's inner steps, per loop type. These mirror
# saage.hydrate.build_step: polling_loop uses "poll"/"status" (not "classify").
_LOOP_KEYS = {
    "retry_loop": ("action", "check"),
    "counting_loop": ("body",),
    "polling_loop": ("poll", "status"),
}


def _leaf_params(spec: dict) -> dict:
    t = spec.get("type")
    if t == "command":
        return {"run": spec.get("run", ""), "set": spec.get("set", {})}
    if t == "agent":
        return {
            "skill": spec.get("skill", ""),
            "set": spec.get("set", {}),
            "max_steps": spec.get("max_steps", ""),
        }
    return {}


def _walk(step: dict, graph: Graph, cluster_id: Optional[str],
          depth: int) -> tuple[Optional[str], Optional[str]]:
    """Add a step (leaf or loop) to the graph, recursing into nested loops.

    Returns (first_leaf_id, last_leaf_id) so callers can chain edges across
    the step regardless of how deeply its contents are nested.
    """
    step_type = step.get("type")

    if step_type in _LOOP_KEYS:
        loop_id = step.get("id")
        graph.clusters.append(Cluster(
            id=loop_id,
            kind=step_type,
            label=loop_id,
            max_iterations=str(step.get("max_iterations", "")),
            depth=depth,
            parent=cluster_id,
        ))
        first_id: Optional[str] = None
        prev_last: Optional[str] = None
        for key in _LOOP_KEYS[step_type]:
            sub_spec = step.get(key)
            if not sub_spec:
                continue
            sub_steps = sub_spec if isinstance(sub_spec, list) else [sub_spec]
            for sub in sub_steps:
                sub_first, sub_last = _walk(sub, graph, loop_id, depth + 1)
                if sub_first is None:
                    continue
                if first_id is None:
                    first_id = sub_first
                if prev_last is not None:
                    graph.edges.append((prev_last, sub_first))
                prev_last = sub_last
        # Loops that repeat until a condition get a back-edge (last → first)
        if step_type in ("retry_loop", "polling_loop") and first_id and prev_last:
            graph.back_edges.append((prev_last, first_id))
        return first_id, prev_last

    # Leaf node (command / agent / anything else)
    node_id = step.get("id")
    graph.nodes.append(GNode(
        id=node_id,
        type=step_type,
        label=node_id,
        params=_leaf_params(step),
        cluster=cluster_id,
    ))
    return node_id, node_id


def build_graph(spec: dict) -> Graph:
    """Walk the workflow spec and build a Graph.

    Recursively processes loops (retry_loop, counting_loop, polling_loop) at
    any nesting depth, creating a cluster per loop and chaining edges through
    each loop's first/last leaf nodes. Retry/polling loops get a back-edge.
    """
    graph = Graph()
    prev_last: Optional[str] = None
    for step in spec.get("workflow", []):
        first_id, last_id = _walk(step, graph, None, 0)
        if first_id is None:
            continue
        if prev_last is not None:
            graph.edges.append((prev_last, first_id))
        prev_last = last_id
    return graph


def reduce_states(events: list[dict]) -> dict[str, dict]:
    """Reduce ledger events to node execution states.
    
    Returns dict[node_id] → {"state": "pending|running|done|failed", "attempts": int, "last": dict}
    
    Rules:
    - phase:start → running
    - phase:end (or legacy no-phase end line) → done unless action == "fail" or exit != 0 → failed
    - A later start on the same node flips failed→running and bumps attempts
    """
    states: dict[str, dict] = {}
    
    for event in events:
        node_id = event.get("node")
        if not node_id:
            continue
        
        if node_id not in states:
            states[node_id] = {"state": "pending", "attempts": 0, "last": {}}
        
        phase = event.get("phase")
        
        if phase == "start":
            # If node was in failed state, flip to running and bump attempts
            if states[node_id]["state"] == "failed":
                states[node_id]["attempts"] += 1
            else:
                if states[node_id]["state"] != "running":
                    states[node_id]["attempts"] += 1
            
            states[node_id]["state"] = "running"
            states[node_id]["last"] = event.copy()
        
        elif phase == "end" or "exit" in event or "action" in event:
            # This is an end event
            # Determine if it's a failure ("fail" from retry checks, "failed"
            # from polling classifiers and terminal failure nodes)
            is_failed = event.get("action") in ("fail", "failed") or event.get("exit", 0) != 0
            
            if is_failed:
                states[node_id]["state"] = "failed"
            else:
                states[node_id]["state"] = "done"
            
            states[node_id]["last"] = event.copy()
    
    return states


def render_svg(graph: Graph, states: dict) -> str:
    """Render graph as standalone SVG with state coloring.
    
    Layout: vertical chain with 180×44 nodes at 24px gaps.
    Clusters wrap their members in a labeled rect.
    """
    # Node dimensions and spacing
    node_width = 180
    node_height = 44
    gap = 24
    x_start = 50
    
    # Compute y positions
    node_y_map: dict[str, int] = {}
    y = 70
    for node in graph.nodes:
        node_y_map[node.id] = y
        y += node_height + gap
    
    svg_lines = []
    svg_lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="{y + 50}">')
    
    # Add CSS for styling
    svg_lines.append('<style>')
    svg_lines.append('.dagnode { font-family: Arial, sans-serif; }')
    svg_lines.append('.dagnode rect { fill: #f0f0f0; stroke: #888; stroke-width: 1; }')
    svg_lines.append('.dagnode text { font-size: 12px; text-anchor: middle; fill: #1a1a1a; }')
    svg_lines.append('.dagnode text.sub { font-size: 10px; fill: #444; }')
    svg_lines.append('.state-done rect { fill: #90EE90; }')
    svg_lines.append('.state-running rect { fill: #FFD700; }')
    svg_lines.append('.state-failed rect { fill: #FFB6C6; }')
    svg_lines.append('.state-pending rect { fill: #D3D3D3; }')
    svg_lines.append('.cluster { fill: none; stroke: #999; stroke-width: 2; stroke-dasharray: 5,5; }')
    svg_lines.append('.backedge { stroke: #999; stroke-width: 1; stroke-dasharray: 5,5; fill: none; }')
    svg_lines.append('.dagnode:focus { outline: none; }')
    svg_lines.append('.dagnode:focus rect { stroke: #4da3ff; stroke-width: 2; }')
    svg_lines.append('</style>')
    
    # Group nodes by cluster, transitively: a node inside a nested loop is a
    # member of every enclosing cluster, so outer rects wrap inner ones.
    parent_of = {c.id: c.parent for c in graph.clusters}
    cluster_nodes: dict[Optional[str], list[str]] = {}
    for node in graph.nodes:
        cluster_id = node.cluster
        while cluster_id is not None:
            cluster_nodes.setdefault(cluster_id, []).append(node.id)
            cluster_id = parent_of.get(cluster_id)

    # Draw cluster rectangles first (background), outer clusters widest
    for cluster in graph.clusters:
        member_ids = cluster_nodes.get(cluster.id, [])
        if member_ids:
            member_ys = [node_y_map[nid] for nid in member_ids]
            min_y = min(member_ys)
            max_y = max(member_ys)
            inset = 6 * cluster.depth
            cluster_x = x_start - 10 + inset
            cluster_y = min_y - 15 + 3 * cluster.depth
            cluster_width = node_width + 20 - 2 * inset
            cluster_height = max_y - min_y + node_height + 30 - 6 * cluster.depth
            svg_lines.append(f'<rect class="cluster" x="{cluster_x}" y="{cluster_y}" '
                           f'width="{cluster_width}" height="{cluster_height}"/>')
            svg_lines.append(f'<text x="{x_start + node_width//2}" y="{cluster_y - 5}" '
                           f'font-size="11" fill="#999" text-anchor="middle">{escape(str(cluster.label))}</text>')
    
    # Draw edges (both regular and back-edges)
    for src_id, dst_id in graph.edges:
        src_y = node_y_map[src_id]
        dst_y = node_y_map[dst_id]
        src_x = x_start + node_width // 2
        dst_x = x_start + node_width // 2
        src_center_y = src_y + node_height // 2
        dst_center_y = dst_y + node_height // 2
        
        svg_lines.append(f'<line x1="{src_x}" y1="{src_center_y + node_height//2}" '
                       f'x2="{dst_x}" y2="{dst_center_y - node_height//2}" '
                       f'stroke="#888" stroke-width="1"/>')
    
    # Draw back-edges with dashed style
    for src_id, dst_id in graph.back_edges:
        src_y = node_y_map[src_id]
        dst_y = node_y_map[dst_id]
        src_x = x_start + node_width // 2
        dst_x = x_start + node_width // 2
        src_center_y = src_y + node_height // 2
        dst_center_y = dst_y + node_height // 2
        
        # Draw back-edge with curved path (goes around right side)
        svg_lines.append(f'<path class="backedge" d="M {src_x} {src_center_y + node_height//2} '
                       f'Q {x_start + node_width + 50} {(src_center_y + dst_center_y) // 2} '
                       f'{dst_x} {dst_center_y - node_height//2}"/>')
    
    # Draw nodes
    for node in graph.nodes:
        state = states.get(node.id, {}).get("state", "pending")
        attempts = states.get(node.id, {}).get("attempts", 0)
        attempts_badge = f" (attempt {attempts})" if attempts > 0 else ""
        
        y = node_y_map[node.id]
        x = x_start
        
        # Flow-authored ids/labels/types are untrusted for markup purposes:
        # the page injects this SVG via innerHTML, so escape everything.
        node_id_attr = quoteattr(str(node.id))
        svg_lines.append(f'<g id={quoteattr(f"node-{node.id}")} class="dagnode state-{state}" '
                         f'data-node={node_id_attr} tabindex="0" role="button" '
                         f'aria-label={quoteattr(f"show parameters for step {node.id}")}>')
        svg_lines.append(f'<rect x="{x}" y="{y}" width="{node_width}" height="{node_height}" rx="8"/>')
        svg_lines.append(f'<text x="{x + node_width//2}" y="{y + 22}">{escape(str(node.label))}</text>')
        svg_lines.append(f'<text x="{x + node_width//2}" y="{y + 36}" class="sub">'
                         f'{escape(f"{node.type}{attempts_badge}")}</text>')
        svg_lines.append('</g>')
    
    svg_lines.append('</svg>')
    return '\n'.join(svg_lines)
