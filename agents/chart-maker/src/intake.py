"""The intake step (code only): traceparent, resume of a pause, data split and caps, and follow-ups
answered from stored state with zero model calls. Returns (state update, route)."""

from __future__ import annotations

import chartflow
import classify
import diagramflow
import hitl
import patch
import prompts
import render
import smalltalk
import steps
from chartspec import Columns, Spec
from dataparse import parse_table
from dataset import Dataset, numeric_columns, x_column
from followup import chart_edit, graph_edit, is_short
from graphspec import Graph
from inputs import split_request, strip_json_cue
from rules import MAX_DATA_CHARS, MAX_ROWS, size_problem
from scope import Scope

END = ""


def intake(state: dict, scope: Scope) -> tuple[dict, str]:
    if not scope.deps.traceparent:
        return steps.say(scope, prompts.NO_TRACE_CONTEXT), END
    if scope.resume is not None:
        return _resume(scope)
    incoming = scope.incoming
    ask, data = (incoming.text, incoming.file_text) if incoming.file_text else split_request(incoming.text)
    scope.ask = strip_json_cue(ask)
    if data:
        if len(data) > MAX_DATA_CHARS:
            return steps.say(scope, size_problem(len(data), 0, 0) or prompts.COULD_NOT), END
        table = parse_table(data)
        if table is not None and numeric_columns(table):
            if len(table.rows) > MAX_ROWS:
                return steps.say(scope, size_problem(0, len(table.rows), 0) or prompts.COULD_NOT), END
            scope.table, scope.original = table, f"{ask}\n\n```\n{data}\n```"
            return {}, "classify"
    followed = _followup(state, scope, incoming.text.strip())
    if followed is not None:
        return followed
    scope.description = incoming.text.strip()
    scope.ask = strip_json_cue(incoming.text)
    return {}, "classify"


def _followup(state: dict, scope: Scope, text: str) -> tuple[dict, str] | None:
    ds, spec = Dataset.from_dict(state.get("dataset")), Spec.from_dict(state.get("spec"))
    if ds is not None and spec is not None and is_short(text):
        edit = chart_edit(text, ds, spec)
        if edit.kind == "csv":
            scope.reply = render.csv_reply(chartflow.as_csv(ds))
            return {}, END
        if edit.kind == "json":
            scope.reply = render.chart_reply(chartflow.build(ds, spec), True)
            return {}, END
        if edit.kind == "chart":
            scope.reply = render.chart_reply(chartflow.build(edit.dataset, edit.spec), scope.as_json)
            return {"dataset": edit.dataset.to_dict(), "spec": edit.spec.to_dict()}, END
        if edit.note:
            return steps.say(scope, edit.note), END
    graph = Graph.from_dict(state.get("graph"))
    if graph is None:
        return None
    edit = graph_edit(text, graph)
    if edit.kind == "json":
        scope.reply = render.diagram_reply(diagramflow.build(graph), graph.kind, True)
        return {}, END
    if edit.kind == "graph":
        scope.reply = render.diagram_reply(diagramflow.build(edit.graph), graph.kind, scope.as_json)
        return {"graph": edit.graph.to_dict()}, END
    if edit.kind == "patch":
        return {}, "patch"
    return None


def _resume(scope: Scope) -> tuple[dict, str]:
    pending, answer = scope.resume
    if hitl.is_cancel(answer):
        return steps.say(scope, "OK, nothing drawn."), END
    if pending.kind == "mode":
        choice = hitl.mode_answer(answer)
        if choice == "chart":
            return steps.say(scope, prompts.NEED_DATA), END
        scope.description = pending.text
        scope.hint = {"flow": "draw it as a diagram of a process (kind flow)",
                      "tree": "draw it as a diagram of a hierarchy (kind tree)"}.get(choice, f"diagram; they added: "
                                                                                     f"{answer[:300]}")
        return {}, "classify"
    ask, data = split_request(pending.text)
    table = parse_table(data)
    picked = hitl.column_answer(pending, answer) if table is not None else None
    if table is None or picked is None:
        names = ", ".join(pending.columns)
        return steps.say(scope, f"I couldn't find a column called \"{answer.strip()[:60]}\". The value columns are: "
                                f"{names}. Send the request again naming one."), END
    columns, stacked = picked
    x = pending.x if pending.x in table.columns else x_column(table)
    return steps.chart_from(scope, table, Columns(x, columns), pending.title, pending.model_type, ask,
                            columns_asked=True, stacked=stacked), END


async def patch_step(state: dict, scope: Scope) -> dict:
    graph = Graph.from_dict(state.get("graph"))
    text = scope.incoming.text.strip()
    result = await classify.patch(scope.deps, scope.budget, graph, text, scope.org)
    if result.get("status") == "off_topic":
        scope.reply = render.message(smalltalk.OFF_TOPIC_REPLY, scope.as_json, needs_human=False)
        return {}
    if result.get("status") != "ok" or not isinstance(result.get("ops"), list):
        return steps.say(scope, "I couldn't apply that change. Say which step to add, remove or connect.")
    description = f"{state.get('description') or ''}\n{text}"
    patched, notes = patch.apply(graph, result["ops"], description)
    out = diagramflow.build(patched, extra_notes=notes)
    scope.reply = render.diagram_reply(out, patched.kind, scope.as_json)
    return {"graph": patched.to_dict(), "description": description[-steps.MAX_DESCRIPTION_STORED:]}
