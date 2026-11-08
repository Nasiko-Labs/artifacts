"""Pure: a validated graph -> Mermaid source, the inline outline, the summary, notes and envelope fields."""

from __future__ import annotations

from dataclasses import dataclass

import mermaid
import outline
from graphspec import Graph, decision_problems, disconnected

KIND_NAMES = {"flow": "Flowchart", "sequence": "Sequence diagram", "tree": "Hierarchy", "timeline": "Timeline",
              "mindmap": "Mind map"}


@dataclass(frozen=True)
class DiagramOut:
    title: str
    summary: str
    mermaid: str
    outline: str
    notes: tuple[str, ...]
    confidence: str
    needs_human: bool
    items: tuple[dict, ...]


def build(graph: Graph, repaired: bool = False, extra_notes: tuple[str, ...] = ()) -> DiagramOut:
    loose = disconnected(graph)
    problems = decision_problems(graph) if graph.kind in ("flow", "tree") else ()
    notes = list(extra_notes)
    notes += [f"({n.id[1:]}) {n.label} is not connected to anything." for n in loose]
    if problems:
        notes.append("Some decisions don't have two labelled exits: " + "; ".join(problems) + ".")
    if graph.dropped_edges:
        notes.append(f"I left out {graph.dropped_edges} link(s) that pointed at a missing step, at the same step, "
                     "or repeated another link.")
    if any(n.inferred for n in graph.nodes):
        notes.append("Labels in italics are my wording, not yours; check them.")
    low = bool(loose and repaired) or bool(problems)
    medium = repaired or bool(loose) or graph.dropped_edges > 0 or any(n.inferred for n in graph.nodes)
    return DiagramOut(
        title=graph.title, summary=summary(graph), mermaid=mermaid.render(graph), outline=outline.render(graph),
        notes=tuple(notes), confidence="low" if low else "medium" if medium else "high", needs_human=low,
        items=tuple({"id": n.id, "label": n.label, "shape": n.shape, "inferred": n.inferred} for n in graph.nodes))


def summary(graph: Graph) -> str:
    decisions = sum(n.shape == "decision" for n in graph.nodes)
    noun = {"sequence": "participants", "timeline": "events", "tree": "items", "mindmap": "ideas"}.get(graph.kind,
                                                                                                      "steps")
    extra = f", {decisions} decision{'s' if decisions != 1 else ''}" if decisions else ""
    links = f", {len(graph.edges)} messages" if graph.kind == "sequence" else ""
    return f"{KIND_NAMES[graph.kind]}: {graph.title} ({len(graph.nodes)} {noun}{extra}{links})"
