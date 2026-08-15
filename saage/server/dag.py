"""DAG builder, ledger state reducer, and SVG renderer for saage server."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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


@dataclass
class Graph:
    """A directed acyclic graph of nodes and clusters."""
    nodes: list[GNode] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)
    back_edges: list[tuple[str, str]] = field(default_factory=list)


def build_graph(spec: dict) -> Graph:
    """Walk the workflow spec and build a Graph.
    
    Recursively processes loops (retry_loop, counting_loop, polling_loop)
    by creating clusters and walking into action/check/poll/classify/body.
    For retry/polling loops, adds a back-edge from the last node back to the first.
    """
    graph = Graph()
    workflow = spec.get("workflow", [])
    
    # Track the previous node to chain edges
    prev_node_id = None
    
    for step in workflow:
        step_id = step.get("id")
        step_type = step.get("type")
        
        if step_type in ("retry_loop", "counting_loop", "polling_loop"):
            # Create a cluster for this loop
            cluster_id = step_id
            max_iter = step.get("max_iterations", "")
            cluster = Cluster(
                id=cluster_id,
                kind=step_type,
                label=step_id,
                max_iterations=str(max_iter)
            )
            graph.clusters.append(cluster)
            
            # Collect nodes to add to this cluster
            loop_first_node_id = None
            loop_last_node_id = None
            
            # Determine which keys to recurse into based on loop type
            if step_type == "retry_loop":
                keys_to_walk = ["action", "check"]
            elif step_type == "counting_loop":
                keys_to_walk = ["body"]
            elif step_type == "polling_loop":
                keys_to_walk = ["poll", "classify"]
            else:
                keys_to_walk = []
            
            loop_nodes = []
            for key in keys_to_walk:
                sub_spec = step.get(key)
                if sub_spec:
                    # Handle both dict and list cases
                    if isinstance(sub_spec, list):
                        loop_nodes.extend(sub_spec)
                    else:
                        loop_nodes.append(sub_spec)
            
            # Process nodes in the loop
            for i, node_spec in enumerate(loop_nodes):
                node_id = node_spec.get("id")
                node_type = node_spec.get("type")
                
                # Extract params based on node type
                if node_type == "command":
                    params = {"run": node_spec.get("run", ""), "set": node_spec.get("set", {})}
                elif node_type == "agent":
                    params = {
                        "skill": node_spec.get("skill", ""),
                        "set": node_spec.get("set", {}),
                        "max_steps": node_spec.get("max_steps", "")
                    }
                else:
                    params = {}
                
                node = GNode(
                    id=node_id,
                    type=node_type,
                    label=node_id,
                    params=params,
                    cluster=cluster_id
                )
                graph.nodes.append(node)
                
                if i == 0:
                    loop_first_node_id = node_id
                    if prev_node_id:
                        graph.edges.append((prev_node_id, node_id))
                else:
                    # Chain previous node to current node
                    if loop_nodes[i-1].get("id"):
                        graph.edges.append((loop_nodes[i-1].get("id"), node_id))
                
                loop_last_node_id = node_id
            
            # Add back-edge for retry/polling loops (from last to first)
            if step_type in ("retry_loop", "polling_loop") and loop_first_node_id and loop_last_node_id:
                graph.back_edges.append((loop_last_node_id, loop_first_node_id))
            
            prev_node_id = loop_last_node_id
        else:
            # Regular node (not a loop)
            node_type = step.get("type")
            
            # Extract params based on node type
            if node_type == "command":
                params = {"run": step.get("run", ""), "set": step.get("set", {})}
            elif node_type == "agent":
                params = {
                    "skill": step.get("skill", ""),
                    "set": step.get("set", {}),
                    "max_steps": step.get("max_steps", "")
                }
            else:
                params = {}
            
            node = GNode(
                id=step_id,
                type=node_type,
                label=step_id,
                params=params,
                cluster=None
            )
            graph.nodes.append(node)
            
            if prev_node_id:
                graph.edges.append((prev_node_id, step_id))
            
            prev_node_id = step_id
    
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
            # Determine if it's a failure
            is_failed = event.get("action") == "fail" or event.get("exit", 0) != 0
            
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
    svg_lines.append('.dagnode rect { fill: #f0f0f0; stroke: #333; stroke-width: 1; }')
    svg_lines.append('.dagnode text { font-size: 12px; text-anchor: middle; }')
    svg_lines.append('.dagnode text.sub { font-size: 10px; fill: #666; }')
    svg_lines.append('.state-done rect { fill: #90EE90; }')
    svg_lines.append('.state-running rect { fill: #FFD700; }')
    svg_lines.append('.state-failed rect { fill: #FFB6C6; }')
    svg_lines.append('.state-pending rect { fill: #D3D3D3; }')
    svg_lines.append('.cluster { fill: none; stroke: #999; stroke-width: 2; stroke-dasharray: 5,5; }')
    svg_lines.append('.backedge { stroke: #999; stroke-width: 1; stroke-dasharray: 5,5; fill: none; }')
    svg_lines.append('</style>')
    
    # Group nodes by cluster
    cluster_nodes: dict[Optional[str], list[str]] = {}
    for node in graph.nodes:
        cluster_id = node.cluster
        if cluster_id not in cluster_nodes:
            cluster_nodes[cluster_id] = []
        cluster_nodes[cluster_id].append(node.id)
    
    # Draw cluster rectangles first (background)
    for cluster in graph.clusters:
        member_ids = cluster_nodes.get(cluster.id, [])
        if member_ids:
            member_ys = [node_y_map[nid] for nid in member_ids]
            min_y = min(member_ys)
            max_y = max(member_ys)
            cluster_x = x_start - 10
            cluster_y = min_y - 15
            cluster_width = node_width + 20
            cluster_height = max_y - min_y + node_height + 30
            svg_lines.append(f'<rect class="cluster" x="{cluster_x}" y="{cluster_y}" '
                           f'width="{cluster_width}" height="{cluster_height}"/>')
            svg_lines.append(f'<text x="{x_start + node_width//2}" y="{cluster_y - 5}" '
                           f'font-size="11" fill="#999" text-anchor="middle">{cluster.label}</text>')
    
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
                       f'stroke="#333" stroke-width="1"/>')
    
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
        
        svg_lines.append(f'<g id="node-{node.id}" class="dagnode state-{state}" data-node="{node.id}">')
        svg_lines.append(f'<rect x="{x}" y="{y}" width="{node_width}" height="{node_height}" rx="8"/>')
        svg_lines.append(f'<text x="{x + node_width//2}" y="{y + 22}">{node.label}</text>')
        svg_lines.append(f'<text x="{x + node_width//2}" y="{y + 36}" class="sub">{node.type}{attempts_badge}</text>')
        svg_lines.append('</g>')
    
    svg_lines.append('</svg>')
    return '\n'.join(svg_lines)
