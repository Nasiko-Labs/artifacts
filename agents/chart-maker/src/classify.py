"""The model calls: classify (mode + column mapping or graph), repair (decision exits) and patch (a
diagram edit). For charts the model sees only the header, three sample rows and the counts, fenced."""

from __future__ import annotations

import json

import prompts
from dataparse import Table
from fence import fence
from graphspec import Graph
from llm import Budget, TurnDeps, ask_json

SAMPLE_ROWS = 3
MAX_DESCRIPTION_CHARS = 6_000


def chart_view(table: Table) -> str:
    """Header, three sample rows and the counts: never the whole dataset."""
    rows = "\n".join(" | ".join(r) for r in table.rows[:SAMPLE_ROWS])
    body = f"columns: {' | '.join(table.columns)}\nsample rows:\n{rows}"
    return fence("DATA", body) + f"\nrow count: {len(table.rows)}; column count: {len(table.columns)}"


async def classify(deps: TurnDeps, budget: Budget, ask: str, table: Table | None, description: str,
                   org: tuple[str, ...], hint: str | None = None) -> dict:
    if table is not None:
        user = f"Request: {ask or '(none: just chart the data)'}\n\nThe user's data:\n{chart_view(table)}"
    else:
        user = "The user's request:\n" + fence("DESCRIPTION", description[:MAX_DESCRIPTION_CHARS])
    if hint:
        user += f"\n\nThe user chose: {hint}."
    messages = [{"role": "system", "content": prompts.system(prompts.CLASSIFY, org)}, {"role": "user", "content": user}]
    return await ask_json(deps, budget, messages) or {}


async def repair(deps: TurnDeps, budget: Budget, description: str, raw_graph: dict, problems: tuple[str, ...],
                 org: tuple[str, ...]) -> dict:
    user = ("The user's request:\n" + fence("DESCRIPTION", description[:MAX_DESCRIPTION_CHARS])
            + "\n\nYour graph:\n" + json.dumps(raw_graph, ensure_ascii=False)
            + "\n\n" + prompts.REPAIR.replace("{problems}", "\n".join(f"- {p}" for p in problems)))
    messages = [{"role": "system", "content": prompts.system(prompts.CLASSIFY, org)}, {"role": "user", "content": user}]
    return await ask_json(deps, budget, messages) or {}


def graph_view(graph: Graph) -> str:
    nodes = "\n".join(f"{n.id}: {n.label} ({n.shape})" for n in graph.nodes)
    edges = "\n".join(f"{e.src} -> {e.dst}" + (f" [{e.label}]" if e.label else "") for e in graph.edges)
    return f"kind: {graph.kind}; direction: {graph.direction}\nnodes:\n{nodes}\nedges:\n{edges or '(none)'}"


async def patch(deps: TurnDeps, budget: Budget, graph: Graph, instruction: str, org: tuple[str, ...]) -> dict:
    user = (fence("DIAGRAM", graph_view(graph)) + "\n\nThe user's change:\n"
            + fence("DESCRIPTION", instruction[:MAX_DESCRIPTION_CHARS]))
    messages = [{"role": "system", "content": prompts.system(prompts.PATCH, org)}, {"role": "user", "content": user}]
    return await ask_json(deps, budget, messages) or {}
