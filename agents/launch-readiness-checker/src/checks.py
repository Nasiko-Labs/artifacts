"""Pure: the twelve checklist rows, computed in code from what the steps read. An area that was not read gives
`unknown` rows, never `pass`. Engineering and support rules live here; docs and marketing in area_checks.py."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import area_checks
from mentions import launch_terms, mentions
from records import Article, Doc, Issue, Macro, Row, Task
from rules import cite, row, unknown

HIGH = frozenset({"urgent", "high"})


@dataclass(frozen=True)
class Inputs:
    """What each area read; None = the area was not read."""

    launch: str
    today: date
    launch_date: date | None = None
    issues: tuple[Issue, ...] | None = None
    docs: tuple[Doc, ...] | None = None
    macros: tuple[Macro, ...] | None = None
    articles: tuple[Article, ...] = ()
    tasks: tuple[Task, ...] | None = None
    marketing_project: str | None = None
    reasons: dict = field(default_factory=dict)   # area -> why it was not read


def evaluate(inputs: Inputs) -> tuple[Row, ...]:
    terms = launch_terms(inputs.launch)
    eng = engineering(inputs.issues) if inputs.issues is not None else unknown(
        "engineering", inputs.reasons.get("engineering", "no tool connected"))
    docs = area_checks.docs(inputs, terms) if inputs.docs is not None else unknown(
        "docs", inputs.reasons.get("docs", "no tool connected"))
    sup = (support(inputs.macros, inputs.articles, terms),) if inputs.macros is not None else unknown(
        "support", inputs.reasons.get("support", "no tool connected"))
    mkt = area_checks.marketing(inputs, terms) if inputs.tasks is not None else unknown(
        "marketing", inputs.reasons.get("marketing", "no tool connected"))
    return (*eng, *docs, *sup, *mkt)


def _ids(issues: Sequence[Issue]) -> str:
    return ", ".join(i.id for i in issues)


def engineering(issues: Sequence[Issue]) -> tuple[Row, ...]:
    open_ = [i for i in issues if i.open]
    high = [i for i in open_ if i.priority in HIGH]
    blocked = [i for i in open_ if "block" in i.state.casefold() or any("block" in lb.casefold() for lb in i.labels)]
    unowned = [i for i in open_ if not i.assignee]
    low = [i for i in open_ if i.priority in {"low", "none"}]
    rows = (
        row("ENG-1", "fail", f"{len(high)} open high issue{'s' * (len(high) != 1)} ({_ids(high)})",
            [cite(i.id, i.title, i.url) for i in high]) if high else row("ENG-1", "pass", "no open urgent/high issues"),
        row("ENG-2", "pass", "no open issues") if not open_ else row(
            "ENG-2", "warn", f"{len(open_)} open, all low/none priority" if len(low) == len(open_)
            else f"{len(open_)} open ({len(high)} urgent/high, see ENG-1)", [cite(i.id, i.title, i.url) for i in open_]),
        row("ENG-3", "fail", f"{len(blocked)} blocked ({_ids(blocked)})", [cite(i.id, i.title, i.url) for i in blocked])
        if blocked else row("ENG-3", "pass", "nothing blocked"),
        row("ENG-4", "warn", f"{len(unowned)} open without an owner ({_ids(unowned)})",
            [cite(i.id, i.title, i.url) for i in unowned]) if unowned else row("ENG-4", "pass", "every open issue has an owner"),
        row("ENG-5", "pass", f"{len(issues)} issue{'s' * (len(issues) != 1)} matched") if issues else
        row("ENG-5", "fail", "no issues matched; give a label or project"),
    )
    return rows


def support(macros: Sequence[Macro], articles: Sequence[Article], terms: tuple[str, ...]) -> Row:
    hits = [cite(m.id, m.title, None) for m in macros if mentions(m.title, m.body, terms)]
    hits += [cite(a.id, a.title, a.url) for a in articles if mentions(a.title, None, terms)]
    name = " ".join(terms)
    if hits:
        return row("SUP-1", "pass", f"{len(hits)} macro/article mention{'s' * (len(hits) != 1)} the launch", hits)
    return row("SUP-1", "fail", f"no macro or article mentions \"{name}\" ({len(macros)} macros, "
                                f"{len(articles)} articles read)")
