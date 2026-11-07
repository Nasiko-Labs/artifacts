"""From stored state to one reply: bind drivers, analyse, build the digest, narrate (model or template), build
the envelope and render. Shared by a new table and every follow-up."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import digest
import drivers
import envelope
import narrate
import render
import sign
import smalltalk
import template
from llm import TurnDeps
from table import Row
from variance import DEFAULT_THRESHOLD, Analysis, Options, analyse


@dataclass(frozen=True)
class View:
    """What this turn shows: narrate with the model (else the code template), table only, JSON."""

    use_model: bool = True
    table_only: bool = False
    wants_json: bool = False
    instruction: str = ""
    notes: tuple[str, ...] = ()
    policy: tuple[str, ...] = ()


@dataclass(frozen=True)
class Outcome:
    reply: str
    data: dict | None = None
    narrative: str = ""
    off_topic: bool = False
    extra: dict = field(default_factory=dict)


def rows_to_state(rows: Sequence[Row]) -> list[dict]:
    return [{"name": r.name, "budget": str(r.budget), "actual": str(r.actual),
             "prior": None if r.prior is None else str(r.prior), "driver": r.driver, "is_total": r.is_total}
            for r in rows]


def rows_from_state(data: Sequence[Mapping[str, Any]] | None) -> tuple[Row, ...]:
    return tuple(Row(str(d["name"]), Decimal(d["budget"]), Decimal(d["actual"]),
                     None if d.get("prior") is None else Decimal(d["prior"]), str(d.get("driver") or ""),
                     bool(d.get("is_total"))) for d in data or ())


def options_of(settings: Mapping[str, Any]) -> Options:
    return Options(threshold=Decimal(str(settings.get("threshold") or DEFAULT_THRESHOLD)),
                   sign_mode=str(settings.get("sign_mode") or sign.AUTO), sign_stated=bool(settings.get("sign_stated")),
                   only=str(settings.get("only") or ""))


def analysis_of(state: Mapping[str, Any]) -> Analysis:
    rows = rows_from_state(state.get("rows"))
    bound = drivers.bind(rows, state.get("message") or "", state.get("added") or {})
    return analyse(rows, options_of(state.get("settings") or {}), bound, state.get("symbol") or "")


def digest_of(state: Mapping[str, Any]) -> digest.Digest:
    settings = state.get("settings") or {}
    return digest.build(analysis_of(state), state.get("symbol") or "", settings.get("period") or "",
                        settings.get("length") or "default")


async def present(state: Mapping[str, Any], deps: TurnDeps, view: View) -> Outcome:
    d = digest_of(state)
    source = state.get("source") or ""
    if view.use_model:
        narration = await narrate.narrate(deps, d, view.instruction, source, view.policy)
        if narration is None:
            return Outcome(smalltalk.OFF_TOPIC_REPLY, off_topic=True)
    else:
        narration = envelope.Narration(template.narrative(d), "template")
    coverage = envelope.Coverage(**(state.get("coverage") or {}))
    data = envelope.build(d, narration, coverage)
    if view.wants_json:
        reply = render.as_json(data)
    else:
        reply = render.render(d, narration, coverage, source, view.table_only, view.notes)
    return Outcome(reply, data, narration.text)


def table_text(d: digest.Digest) -> str:
    """The stored table, re-printed for a follow-up question (figures as the digest formats them)."""
    lines = ["Line | Budget | Actual | Variance | % | F/U | Driver"]
    for item in d.analysis.items:
        ln = digest.line("", item, d.symbol)
        lines.append(f"{item.name} | {ln.budget} | {ln.actual} | {ln.signed} | {ln.signed_pct} | {item.direction} | "
                     f"{item.driver or 'not provided'}")
    return "\n".join(lines)
