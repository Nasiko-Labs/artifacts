"""An analysis turn: incidents in the window (live from the incident pager, or a pasted export), grouped
into alerts, the candidate incidents' logs read, noise computed and proposals built, all in code. The
model drives the read tools and writes one optional headline without numbers.

The report (a plain dict, checkpointed per conversation) is what follow-ups read: "show incidents for
X", pasted metric values, "open the PR for #1".
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import noise
import pasted
import prompts
import propose
import render
import steps
from asks import Ask
from deps import TurnDeps
from discovery import Discoverer
from noise import AlertGroup, AlertStats, LogFacts, Params
from reader import TurnReader, ask_once

log = logging.getLogger("alert_tuner.analysis")

MAX_LISTED_INCIDENTS = 50


@dataclass(frozen=True)
class Analysis:
    reply: str
    report: dict | None = None


async def analyze(deps: TurnDeps, ask: Ask, text: str) -> Analysis:
    export = pasted.export(text)
    values = pasted.metric_values(text) if export is None else {}
    if export is not None:
        coverage = ["Only the first rows of the export were read (cap)."] if export.truncated else []
        groups = noise.group(export.incidents)
        return await _finish(deps, ask.params, groups, export.facts, coverage, "pasted", values)
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Analysis(failure)
    reader = TurnReader(deps, discoverer)
    found = await steps.read_incidents(reader, ask.params)
    if not found.ran:
        return Analysis(prompts.NO_PAGER)
    if not found.items and not found.listed:  # the tools offered returned no incident data at all
        notes = "".join(f"\n- {n}" for n in found.notes)
        return Analysis(prompts.NO_INCIDENT_DATA + (f"\n\nCoverage:{notes}" if notes else ""))
    groups = noise.group(found.items)
    logs = await steps.read_logs(reader, noise.log_candidates(groups, ask.params.min_firings))
    return await _finish(deps, ask.params, groups, logs.facts, [*found.notes, *logs.notes], "live", values)


async def _finish(deps: TurnDeps, params: Params, groups: Sequence[AlertGroup], facts: Mapping[str, LogFacts],
                  coverage: Sequence[str], source: str, values: Mapping[str, Sequence[float]]) -> Analysis:
    result = noise.analyse(groups, facts, params)
    report = build_report(params, groups, result, coverage, source, values)
    if not result.noisy:
        text = prompts.NO_NOISY.format(n=params.min_firings, days=params.window_days)
        return Analysis(render.no_noisy(report, text), report)
    report = {**report, "headline": await headline(deps, report)}
    return Analysis(render.markdown(report), report)


def build_report(params: Params, groups: Sequence[AlertGroup], result: noise.NoiseResult, coverage: Sequence[str],
                 source: str, values: Mapping[str, Sequence[float]]) -> dict:
    judged = (*result.noisy, *result.chatty)
    by_name = {g.name: g for g in groups}
    return {
        "params": {"window_days": params.window_days, "min_firings": params.min_firings, "service": params.service,
                   "include_ack_only": params.include_ack_only},
        "noisy": [s.to_dict() for s in result.noisy],
        "chatty": [s.to_dict() for s in result.chatty],
        "too_unknown": [s.alert for s in result.too_unknown],
        "total_alerts": result.total_alerts,
        "proposals": propose.for_all(result.noisy, params.min_firings, values),
        "coverage": list(coverage),
        "source": source,
        "headline": "",
        "durations": {s.alert: list(s.zero_durations) for s in result.noisy},
        "incidents": {s.alert: _rows(by_name.get(s.alert), s) for s in judged},
        "pasted": {k: list(v) for k, v in values.items()},
    }


def _rows(group: AlertGroup | None, s: AlertStats) -> list[dict]:
    if group is None:
        return []
    rows = [{"number": i.label, "url": i.url, "created": i.created, "verdict": s.verdicts.get(i.id, "unchecked"),
             "duration_min": noise.duration_min(i)} for i in reversed(group.incidents)]
    return rows[:MAX_LISTED_INCIDENTS]


async def headline(deps: TurnDeps, report: Mapping) -> str:
    """At most two sentences without a number; anything else is dropped (the code summary stands)."""
    messages = ({"role": "system", "content": prompts.HEADLINE_SYSTEM},
                {"role": "user", "content": prompts.fence_facts(render.facts_for_headline(report))})
    text = (await ask_once(deps, messages)).strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    line = str(data.get("headline") or "").strip() if isinstance(data, dict) else ""
    if not render.headline_ok(line):
        log.info("headline dropped by the facts check")
        return ""
    return line


def stats_of(report: Mapping) -> list[AlertStats]:
    """The noisy alerts' stats back from the stored report (for re-proposing with pasted values)."""
    durations = report.get("durations") or {}
    out = []
    for d in report.get("noisy") or ():
        fields = {k: d[k] for k in AlertStats.__dataclass_fields__ if k in d and k not in ("zero_durations", "verdicts")}
        out.append(AlertStats(**{**fields, "evidence": tuple(d.get("evidence") or ())},
                              zero_durations=tuple(durations.get(d["alert"]) or ())))
    return out


def with_values(report: Mapping, values: Mapping[str, Sequence[float]]) -> dict:
    """The stored report with the proposals recomputed from newly pasted metric values."""
    merged = {**(report.get("pasted") or {}), **{k: list(v) for k, v in values.items()}}
    proposals = propose.for_all(stats_of(report), int(report["params"]["min_firings"]), merged)
    return {**report, "proposals": proposals, "pasted": merged}


def unmatched_values(report: Mapping, values: Mapping[str, Sequence[float]]) -> list[str]:
    names = [s["alert"] for s in report.get("noisy") or ()]
    return [k for k in values if not any(propose.values_for(n, {k: values[k]}) for n in names)]


def show(report: Mapping, wanted: str) -> str:
    """One alert's incidents from the stored report: zero tool and LLM calls."""
    needle = wanted.strip().strip("\"'`").casefold()
    listed = report.get("incidents") or {}
    name = next((n for n in listed if n.casefold() == needle), None) or next(
        (n for n in listed if needle and (needle in n.casefold() or n.casefold() in needle)), None)
    if name is None:
        known = ", ".join(render.code(n) for n in listed) or "none"
        return f"I have no alert matching {render.code(wanted)} in the last analysis. Alerts I can show: {known}."
    rows = listed[name]
    lines = [f"**Incidents for {render.code(name)}** (newest first, {len(rows)} shown)"]
    for r in rows:
        number = render.evidence_line([{"number": r["number"], "url": r["url"]}])
        lines.append(f"- {number} · created {r['created']} · {r['verdict']} · lasted {render.minutes(r['duration_min'])}")
    return "\n".join(lines)
