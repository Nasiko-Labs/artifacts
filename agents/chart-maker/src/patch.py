"""Pure: applying the model's patch ops to a stored graph. Every op is validated; an op naming a node
that doesn't exist is skipped and reported, never guessed."""

from __future__ import annotations

from dataclasses import replace

from graphspec import (
    DIRECTIONS,
    MAX_NODES,
    SHAPES,
    Graph,
    Node,
    add_edges,
    resolve,
    traces_to,
)

OPS = ("add_node", "remove_node", "rename", "add_edge", "remove_edge", "set_direction", "set_shape")


def apply(graph: Graph, ops: list, description: str) -> tuple[Graph, tuple[str, ...]]:
    """(patched graph, notes about skipped ops)."""
    refs: dict[str, str] = {}
    notes: list[str] = []
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict) or op.get("op") not in OPS:
            notes.append("I skipped a change I couldn't read.")
            continue
        graph, note = _apply_one(graph, op, refs, description)
        if note:
            notes.append(note)
    return graph, tuple(notes)


def _apply_one(graph: Graph, op: dict, refs: dict[str, str], description: str) -> tuple[Graph, str | None]:
    kind = op["op"]
    if kind == "add_node":
        return _add_node(graph, op, refs, description)
    if kind == "add_edge":
        before = graph.dropped_edges
        patched = add_edges(graph, [op], refs)
        return patched, (None if patched.dropped_edges == before else "I skipped a link to a step that isn't there.")
    if kind == "set_direction":
        direction = str(op.get("direction") or "").upper()
        return (replace(graph, direction=direction), None) if direction in DIRECTIONS else (graph, None)
    node_id = resolve(graph, refs, op.get("node"))
    if kind == "remove_edge":
        dst = resolve(graph, refs, op.get("to"))
        src = resolve(graph, refs, op.get("from"))
        edges = tuple(e for e in graph.edges if not (e.src == src and e.dst == dst))
        return replace(graph, edges=edges), (None if len(edges) < len(graph.edges) else "That link isn't there.")
    if node_id is None:
        return graph, f"I couldn't find the step \"{op.get('node')}\", so I left the diagram as it was there."
    if kind == "remove_node":
        nodes = tuple(n for n in graph.nodes if n.id != node_id)
        edges = tuple(e for e in graph.edges if node_id not in (e.src, e.dst))
        groups = tuple((g, tuple(m for m in members if m != node_id)) for g, members in graph.groups)
        return replace(graph, nodes=nodes, edges=edges, groups=groups), None
    if kind == "rename":
        label = " ".join(str(op.get("label") or "").split())[:80]
        if not label:
            return graph, None
        return _swap(graph, node_id, lambda n: replace(n, label=label, inferred=not traces_to(label, description))), None
    shape = op.get("shape")
    if shape not in SHAPES:
        return graph, None
    return _swap(graph, node_id, lambda n: replace(n, shape=shape)), None


def _add_node(graph: Graph, op: dict, refs: dict[str, str], description: str) -> tuple[Graph, str | None]:
    if len(graph.nodes) >= MAX_NODES:
        return graph, f"The diagram already has {MAX_NODES} steps, the most I draw; remove one first."
    label = " ".join(str(op.get("label") or "").split())[:80]
    if not label:
        return graph, None
    number = max((int(n.id[1:]) for n in graph.nodes), default=0) + 1
    node = Node(f"N{number}", label, op.get("shape") if op.get("shape") in SHAPES else "step",
                not traces_to(label, description))
    refs[str(op.get("ref") or op.get("id") or node.id)] = node.id
    return replace(graph, nodes=(*graph.nodes, node)), None


def _swap(graph: Graph, node_id: str, change) -> Graph:
    return replace(graph, nodes=tuple(change(n) if n.id == node_id else n for n in graph.nodes))

