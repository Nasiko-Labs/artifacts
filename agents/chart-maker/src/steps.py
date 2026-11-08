"""The turn's steps (graph nodes call these). Each returns the state to persist and leaves this turn's
reply or pause on the scope. Persisted state: the parsed dataset and chart spec, or the diagram graph
and the user's description, so follow-ups need no re-paste."""

from __future__ import annotations

import re

import chartflow
import classify
import diagramflow
import graphspec
import prompts
import render
import smalltalk
from chartspec import Columns, Spec, checked_title, choose_columns, default_title
from dataparse import Table
from dataset import build_dataset, numeric_columns, x_column
from graphspec import decision_problems
from hitl import Pending
from rules import MAX_SERIES, size_problem
from scope import Scope

MAX_DESCRIPTION_STORED = 6_000
_TOP = re.compile(r"\btop\s+(\d{1,3})\b", re.IGNORECASE)
_SORT_LABEL = re.compile(r"\b(?:alphabetical(?:ly)?|sort(?:ed)? by (?:name|label))\b", re.IGNORECASE)

CLEARED_CHART = {"dataset": None, "spec": None}
CLEARED_GRAPH = {"graph": None, "description": None}


def say(scope: Scope, text: str) -> dict:
    scope.reply = render.message(text, scope.as_json)
    return {}


def chart_from(scope: Scope, table: Table, cols: Columns, title: str | None, model_type: str | None, ask: str,
               columns_asked: bool = False, stacked: bool = False) -> dict:
    if not cols.y:
        return say(scope, prompts.NEED_DATA)
    problem = size_problem(0, len(table.rows), len(cols.y))
    if problem:
        return say(scope, problem)
    ds = build_dataset(table, cols.x, cols.y)
    if not ds.labels:
        return say(scope, "None of the rows had a number I could read, so there's nothing to chart. Check the "
                          "value column and paste it again.")
    allowed = ask + "\n" + "\n".join(" ".join(r) for r in table.rows) + " ".join(table.columns)
    top = _TOP.search(ask)
    spec = Spec(title=checked_title(title, default_title(cols.x, cols.y), allowed),
                type_ask="stacked" if stacked else ask, model_type=model_type,
                sort="label" if _SORT_LABEL.search(ask) else None, top_n=int(top.group(1)) if top else None,
                columns_asked=columns_asked)
    out = chartflow.build(ds, spec, scope.incoming.notes)
    scope.reply = render.chart_reply(out, scope.as_json)
    return {"dataset": ds.to_dict(), "spec": spec.to_dict(), **CLEARED_GRAPH}


async def diagram_from(scope: Scope, raw_graph: dict, description: str) -> dict:
    built = graphspec.build(raw_graph, description)
    if built.graph is None:
        return say(scope, built.problem or prompts.COULD_NOT)
    graph, repaired = built.graph, False
    problems = decision_problems(graph) if graph.kind in ("flow", "tree") else ()
    if problems and scope.budget.can_call():
        fixed = await classify.repair(scope.deps, scope.budget, description, raw_graph, problems, scope.org)
        rebuilt = graphspec.build(fixed.get("graph") or {}, description) if isinstance(fixed.get("graph"), dict) else None
        if rebuilt is not None and rebuilt.graph is not None:
            graph = rebuilt.graph
        repaired = True
    out = diagramflow.build(graph, repaired=repaired)
    scope.reply = render.diagram_reply(out, graph.kind, scope.as_json)
    return {"graph": graph.to_dict(), "description": description[:MAX_DESCRIPTION_STORED], **CLEARED_CHART}


async def classify_step(scope: Scope) -> dict:
    result = await classify.classify(scope.deps, scope.budget, scope.ask, scope.table, scope.description,
                                     scope.org, scope.hint)
    status = result.get("status")
    if status == "off_topic":
        scope.reply = render.message(smalltalk.OFF_TOPIC_REPLY, scope.as_json, needs_human=False)
        return {}
    if status == "needs_info" and scope.table is None:
        if scope.no_more_pauses:
            return say(scope, prompts.COULD_NOT)
        scope.pending = Pending("mode", scope.description, scope.org, scope.as_json)
        return {}
    if status != "ok":
        return say(scope, prompts.COULD_NOT)
    if scope.table is not None:
        return _chart_mode(scope, result)
    if result.get("mode") == "chart":
        return say(scope, prompts.NEED_DATA)
    if not isinstance(result.get("graph"), dict):
        return say(scope, prompts.COULD_NOT)
    return await diagram_from(scope, result["graph"], scope.description)


def _chart_mode(scope: Scope, result: dict) -> dict:
    chart = result.get("chart") if isinstance(result.get("chart"), dict) else {}
    table = scope.table
    cols = choose_columns(table, scope.ask, chart)
    model_type = chart.get("type") if chart.get("type") in chartflow.TYPE_NAMES else None
    if cols is not None:
        return chart_from(scope, table, cols, chart.get("title"), model_type, scope.ask)
    x = chart.get("x") if chart.get("x") in table.columns else x_column(table)
    offered = tuple(c for c in numeric_columns(table) if c != x)
    if len(offered) > MAX_SERIES:
        return say(scope, size_problem(0, 0, len(offered)) or prompts.COULD_NOT)
    scope.pending = Pending("column", scope.original, scope.org, scope.as_json, offered,
                            str(chart.get("title") or ""), model_type, x)
    return {}
