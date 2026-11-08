"""Pure: the plain-text outline shown inline (Mermaid isn't rendered in chat). Steps are numbered by
their node id, walked from the start; a branch is indented under its decision with the edge label, and
a link back to a step already listed reads "→ goes to (1) Merge". Labels not in the user's words are
italicised. Sequence diagrams read as "A → B: message" lines."""

from __future__ import annotations

from fmt import md
from graphspec import Graph, Node, disconnected


def _name(node: Node) -> str:
    return f"*{md(node.label)}*" if node.inferred else md(node.label)


def _ref(node: Node) -> str:
    return f"({node.id[1:]}) {_name(node)}"


def render(graph: Graph) -> str:
    if graph.kind == "sequence":
        return "\n".join(f"- {_name(graph.node(e.src))} → {_name(graph.node(e.dst))}: {md(e.label or 'message')}"
                         for e in graph.edges) or "- (no messages)"
    if graph.kind == "timeline":
        return "\n".join(f"- {_name(n)}" for n in graph.nodes)
    lines: list[str] = []
    seen: set[str] = set()
    outgoing: dict[str, list] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.src, []).append(edge)

    def walk(node_id: str, depth: int, via: str | None) -> None:
        indent = "   " * depth
        prefix = f"{md(via)}: " if via else ""
        node = graph.node(node_id)
        if node_id in seen:
            lines.append(f"{indent}- {prefix}→ goes to {_ref(node)}")
            return
        seen.add(node_id)
        lines.append(f"{indent}- {prefix}{_ref(node)}")
        exits = outgoing.get(node_id, [])
        branch = len(exits) > 1
        for edge in exits:
            walk(edge.dst, depth + 1 if branch else depth, edge.label if branch else None)

    for start in _starts(graph):
        walk(start, 0, None)
    lost = [n for n in graph.nodes if n.id not in seen]
    for node in lost:
        walk(node.id, 0, None)
    return "\n".join(lines)


def _starts(graph: Graph) -> list[str]:
    targets = {e.dst for e in graph.edges}
    alone = {n.id for n in disconnected(graph)}
    starts = [n.id for n in graph.nodes if n.id not in targets and n.id not in alone]
    return starts or [graph.nodes[0].id]
