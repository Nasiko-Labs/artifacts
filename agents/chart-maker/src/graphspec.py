"""Pure: the diagram graph. The model proposes labels and edges; code assigns ids N1..Nn, drops (and
counts) edges to missing nodes, self-loops and duplicates, caps the node count, flags labels that are
not in the user's words, and checks decisions and connectivity."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace

MAX_NODES = 14
MAX_LABEL_CHARS = 80
KINDS = ("flow", "sequence", "tree", "timeline", "mindmap")
SHAPES = ("step", "decision", "start_end", "actor")
DIRECTIONS = ("LR", "TD")
INFERRED_OVERLAP = 0.6
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    shape: str = "step"
    inferred: bool = False


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    label: str | None = None


@dataclass(frozen=True)
class Graph:
    kind: str
    direction: str
    title: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...] = ()
    groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    dropped_edges: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> Graph | None:
        if not data:
            return None
        return Graph(kind=data["kind"], direction=data["direction"], title=data["title"],
                     nodes=tuple(Node(**n) for n in data["nodes"]), edges=tuple(Edge(**e) for e in data["edges"]),
                     groups=tuple((g[0], tuple(g[1])) for g in data.get("groups") or ()),
                     dropped_edges=int(data.get("dropped_edges", 0)))

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)


@dataclass(frozen=True)
class Built:
    graph: Graph | None
    problem: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def build(raw: dict, description: str, title: str = "") -> Built:
    kind = raw.get("kind") if raw.get("kind") in KINDS else "flow"
    direction = raw.get("direction") if raw.get("direction") in DIRECTIONS else ("TD" if kind == "tree" else "LR")
    nodes_in = [n for n in raw.get("nodes") or [] if isinstance(n, dict) and _clean(n.get("label"))]
    if len(nodes_in) > MAX_NODES:
        return Built(None, f"That has {len(nodes_in)} steps; I draw at most {MAX_NODES} so the diagram stays "
                           "readable. Split it into two parts (for example up to a hand-off) and I'll draw each.")
    if not nodes_in:
        return Built(None, "I couldn't find any steps or items to draw. List them, for example \"A, then B, then C\".")
    refs: dict[str, str] = {}
    nodes = []
    for k, item in enumerate(nodes_in, start=1):
        node_id = f"N{k}"
        label = _clean(item.get("label"))
        refs[str(item.get("id") or node_id)] = node_id
        refs.setdefault(label.casefold(), node_id)
        shape = item.get("shape") if item.get("shape") in SHAPES else "step"
        nodes.append(Node(node_id, label, shape, not traces_to(label, description)))
    graph = Graph(kind, direction, _clean(title or raw.get("title")) or "Diagram", tuple(nodes))
    graph = add_edges(graph, raw.get("edges") or [], refs)
    groups = tuple((_clean(g.get("label")) or "Group", tuple(refs[str(r)] for r in g.get("nodes") or ()
                                                              if str(r) in refs))
                   for g in raw.get("groups") or [] if isinstance(g, dict))
    return Built(replace(graph, groups=tuple(g for g in groups if g[1])))


def add_edges(graph: Graph, edges: list, refs: dict[str, str]) -> Graph:
    known = {(e.src, e.dst) for e in graph.edges}
    added, dropped = list(graph.edges), graph.dropped_edges
    for item in edges:
        src = resolve(graph, refs, item.get("from")) if isinstance(item, dict) else None
        dst = resolve(graph, refs, item.get("to")) if isinstance(item, dict) else None
        if src is None or dst is None or src == dst or (src, dst) in known:
            dropped += 1
            continue
        known.add((src, dst))
        added.append(Edge(src, dst, _clean(item.get("label")) or None))
    return replace(graph, edges=tuple(added), dropped_edges=dropped)


def resolve(graph: Graph, refs: dict[str, str], ref) -> str | None:
    if ref is None:
        return None
    key = str(ref).strip()
    if key in refs:
        return refs[key]
    if key.casefold() in refs:
        return refs[key.casefold()]
    ids = {n.id for n in graph.nodes}
    if key.upper() in ids:
        return key.upper()
    return next((n.id for n in graph.nodes if n.label.casefold() == key.casefold()), None)


def traces_to(label: str, description: str) -> bool:
    """True when the label is the user's own words: a normalised substring, or >= 60% of its words."""
    words, source = _WORD.findall(label.casefold()), " ".join(_WORD.findall((description or "").casefold()))
    if not words:
        return False
    if " ".join(words) in source:
        return True
    present = set(source.split())
    return sum(w in present for w in words) / len(words) >= INFERRED_OVERLAP


def decision_problems(graph: Graph) -> tuple[str, ...]:
    problems = []
    for node in graph.nodes:
        if node.shape != "decision":
            continue
        exits = [e for e in graph.edges if e.src == node.id]
        if len(exits) < 2 or not all(e.label for e in exits):
            problems.append(f"decision {node.id} ({node.label}) needs at least two outgoing edges, each with a "
                            f"label such as yes/no; it has {len(exits)}")
    return tuple(problems)


def disconnected(graph: Graph) -> tuple[Node, ...]:
    if len(graph.nodes) < 2:
        return ()
    linked = {e.src for e in graph.edges} | {e.dst for e in graph.edges}
    return tuple(n for n in graph.nodes if n.id not in linked)


def _clean(value) -> str:
    return " ".join(str(value or "").split())[:MAX_LABEL_CHARS]
