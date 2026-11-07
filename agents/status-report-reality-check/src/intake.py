"""Pure: what this turn does, decided in code before any model call.

    greeting -> intro | report + export -> run (or a pause: Q1 column, Q2 row cap, Q3 missing half)
    stored result + a config phrase -> re-compare | stored result + a follow-up -> followup
    anything else -> scope (one classify call)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import followup
import options as options_mod
import pauses
import prompts
import smalltalk
import split
import statuses
from inputs import Incoming, word_count
from options import Options
from parse_export import most_recent
from parse_export import parse as parse_export_text

MIN_REPORT_WORDS = 60
RECOMPARE_MAX_EXTRA_WORDS = 12


@dataclass(frozen=True)
class Route:
    kind: str                       # reply | pause | run | recompare | followup | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    report: str = ""
    request: str = ""
    tickets: list = field(default_factory=list)
    options: Options = field(default_factory=Options)
    meta: dict = field(default_factory=dict)
    command: followup.Command | None = None


def plan_turn(incoming: Incoming, state: dict) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    has_result = bool(state.get("envelope"))
    fresh = options_mod.parse(incoming.text, Options())
    pasted = split.split(fresh.text)
    has_export = bool(pasted.export.strip())
    looks_like_report = (word_count(pasted.report) >= MIN_REPORT_WORDS
                         and statuses.reported_category(pasted.report) != statuses.UNKNOWN)
    if has_export or looks_like_report:
        return prepare(pasted.report, pasted.export, pasted.preamble, fresh.options, {}, False, incoming.wants_json)
    if has_result:
        stored = options_mod.parse(incoming.text, Options.from_state(state.get("options")))
        if stored.changed and word_count(stored.text) <= RECOMPARE_MAX_EXTRA_WORDS:
            return Route("recompare", options=stored.options)
        command = followup.parse(incoming.text)
        if command is not None:
            return Route("followup", command=command)
    return Route("scope")


def plan_resume(answer: pauses.Answer, pending: pauses.Pending) -> Route:
    if answer.action != "run":
        return Route("reply", prompts.CANCELLED)
    export = (split.split(answer.export).export or answer.export) if answer.export else ""
    report = split.unwrap_report(answer.report)
    return prepare(report, export, pending.request, Options.from_state(pending.options), answer.columns,
                   answer.use_recent, pending.wants_json)


def prepare(report: str, export: str, request: str, opts: Options, columns: dict, use_recent: bool,
            wants_json: bool) -> Route:
    base = pauses.Pending(pauses.MISSING_HALF, report=report, export=export, request=request,
                          options=opts.to_state(), columns=dict(columns), wants_json=wants_json)
    if not export.strip():
        return Route("pause", pending=_with(base, missing="export"))
    if word_count(report) < MIN_REPORT_WORDS:
        return Route("pause", pending=_with(base, missing="report"))
    parsed = parse_export_text(export, columns)
    if parsed.ambiguous:
        name, candidates = parsed.ambiguous
        return Route("pause", pending=_with(base, kind=pauses.COLUMN, field_name=name, candidates=candidates))
    if parsed.error:
        return Route("reply", parsed.error + "\n\n" + prompts.NEED_BOTH)
    tickets, meta = parsed.tickets, {"parse_caveats": list(parsed.caveats)}
    if len(tickets) > pauses.ROW_CAP_LIMIT:
        if not use_recent:
            return Route("pause", pending=_with(base, kind=pauses.ROW_CAP, rows=len(tickets)))
        meta = {**meta, "trimmed_from": len(tickets)}
        tickets = most_recent(tickets, pauses.ROW_CAP_LIMIT)
    return Route("run", report=report, request=request, tickets=tickets, options=opts, meta=meta)


def _with(base: pauses.Pending, **changes) -> pauses.Pending:
    return replace(base, **changes)
