"""Pure: follow-ups handled in code with zero model calls ("line chart", "top 3", "sort by name",
"swap axes", "as csv", "rename X to Y", "top-down"). Anything else that edits a diagram goes to one
patch call; anything else is a new request."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from chartspec import Spec
from dataset import Dataset, rename_label, transpose
from graphspec import Graph
from rules import choose, explicit_type

MAX_FOLLOWUP_WORDS = 12
_TOP = re.compile(r"\b(?:top|first|largest|biggest)\s+(\d{1,3})\b", re.IGNORECASE)
_SHOW_ALL = re.compile(r"\b(?:show (?:all|everything)|all (?:categories|rows))\b", re.IGNORECASE)
_SORT_LABEL = re.compile(r"\bsort(?:ed)? (?:by )?(?:name|label|alphabetic(?:al(?:ly)?)?|a-z)\b|\balphabetical", re.IGNORECASE)
_SORT_VALUE = re.compile(r"\bsort(?:ed)? (?:by )?(?:value|size|largest|biggest|amount)\b", re.IGNORECASE)
_SWAP = re.compile(r"\b(?:swap|flip|switch) (?:the )?axes\b|\btranspose\b", re.IGNORECASE)
_CSV = re.compile(r"\b(?:as|in|to|export(?: it)?(?: as)?) csv\b|^\s*csv\s*$", re.IGNORECASE)
_JSON = re.compile(r"^\s*(?:show it |give it (?:to me )?|return it )?(?:as )?json(?: only)?\s*[.!]?\s*$", re.IGNORECASE)
_RENAME = re.compile(r"^\s*rename\s+[\"']?(.+?)[\"']?\s+(?:to|as)\s+[\"']?(.+?)[\"']?\s*[.!]?\s*$", re.IGNORECASE)
_TITLE = re.compile(r"^\s*(?:title it|call it|set the title to|title:)\s+[\"']?(.+?)[\"']?\s*[.!]?\s*$", re.IGNORECASE)
_TOP_DOWN = re.compile(r"\b(?:top[- ]down|top to bottom|vertical(?:ly)?)\b", re.IGNORECASE)
_LEFT_RIGHT = re.compile(r"\b(?:left[- ]to[- ]right|horizontal(?:ly)?|sideways)\b", re.IGNORECASE)
_EDIT = re.compile(r"\b(?:add|insert|remove|delete|drop|connect|link|make|turn|change|rename|move|replace|put|split|"
                   r"merge|after|before|between)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Edit:
    kind: str                       # chart | csv | json | graph | none
    dataset: Dataset | None = None
    spec: Spec | None = None
    graph: Graph | None = None
    note: str | None = None


def is_short(text: str) -> bool:
    return 0 < len((text or "").split()) <= MAX_FOLLOWUP_WORDS


def chart_edit(text: str, ds: Dataset, spec: Spec) -> Edit:
    if _CSV.search(text):
        return Edit("csv", ds, spec)
    if _JSON.match(text):
        return Edit("json", ds, spec)
    rename = _RENAME.match(text)
    if rename:
        renamed = rename_label(ds, rename.group(1), rename.group(2))
        if renamed is None:
            return Edit("none", note=f"There's no label or column called \"{rename.group(1)}\" in the chart.")
        return Edit("chart", renamed, spec)
    title = _TITLE.match(text)
    if title:
        return Edit("chart", ds, replace(spec, title=" ".join(title.group(1).split())[:60]))
    changed, out_ds, out_spec = False, ds, spec
    if _SWAP.search(text):
        changed = True
        if len(ds.series) > 1:
            out_ds = transpose(ds)
        else:
            out_spec = replace(out_spec, type_ask="column" if _current_is_bar(spec, ds) else "bar")
    wanted = explicit_type(text)
    if wanted and not _SWAP.search(text):
        out_spec, changed = replace(out_spec, type_ask=text), True
    top = _TOP.search(text)
    if top:
        out_spec, changed = replace(out_spec, top_n=max(1, int(top.group(1)))), True
    if _SHOW_ALL.search(text):
        out_spec, changed = replace(out_spec, top_n=None), True
    if _SORT_LABEL.search(text):
        out_spec, changed = replace(out_spec, sort="label"), True
    elif _SORT_VALUE.search(text):
        out_spec, changed = replace(out_spec, sort="value"), True
    return Edit("chart", out_ds, out_spec) if changed else Edit("none")


def _current_is_bar(spec: Spec, ds: Dataset) -> bool:
    return choose(ds, spec.type_ask, spec.model_type).type != "column"


def graph_edit(text: str, graph: Graph) -> Edit:
    if _JSON.match(text):
        return Edit("json", graph=graph)
    if _TOP_DOWN.search(text) and is_short(text) and not _EDIT.search(text.replace("make it", "")):
        return Edit("graph", graph=replace(graph, direction="TD"))
    if _LEFT_RIGHT.search(text) and is_short(text) and not _EDIT.search(text.replace("make it", "")):
        return Edit("graph", graph=replace(graph, direction="LR"))
    title = _TITLE.match(text)
    if title:
        return Edit("graph", graph=replace(graph, title=" ".join(title.group(1).split())[:60]))
    if _EDIT.search(text):
        return Edit("patch", graph=graph)
    return Edit("none")
