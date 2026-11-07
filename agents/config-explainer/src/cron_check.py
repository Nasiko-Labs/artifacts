"""Pure: the full cron analysis (phrase, next runs, frequency, gotchas C01–C10). No model call: every word
of a cron reply is built here."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import cron_runs as runs
from analysis import Analysis
from cron import CronError, CronSpec, parse
from cron_phrase import MONTH_NAMES, phrase
from gotchas import Gotcha, ordered
from probes import Probes

_OTHER_DIALECT = re.compile(r"\b(quartz|spring|aws|eventbridge|cloudwatch)\b", re.IGNORECASE)
_GITHUB = re.compile(r"\bgithub\b", re.IGNORECASE)
FULL = {"minute": "0-59", "hour": "0-23", "day-of-month": "1-31", "month": "1-12", "day-of-week": "0-6"}


def analyze(config: str, probes: Probes, now: datetime) -> Analysis:
    try:
        spec = parse(config)
    except CronError as exc:
        return Analysis("cron", config, {}, parse_error=str(exc), summary=f"Cron: can't parse ({exc}).")
    if probes.tz and not spec.tz:
        spec = _with_tz(spec, probes.tz)
    try:
        tz = runs.zone(spec.tz)
    except ValueError as exc:
        return Analysis("cron", config, {}, parse_error=str(exc), summary=f"Cron: {exc}.")
    text = phrase(spec)
    zone_name = spec.tz or "UTC"
    if spec.reboot or spec.quartz:
        gotchas = [_c07()] if spec.reboot else []
        return Analysis("cron", config, {"phrase": text, "timezone": zone_name}, tuple(gotchas),
                        summary=f"Cron: {text}", explanation=text, unsupported=spec.quartz)
    start = (probes.start.replace(tzinfo=tz) if probes.start else now).astimezone(tz)
    count = min(probes.count or runs.DEFAULT_RUNS, runs.MAX_RUNS)
    upcoming, notes = runs.next_runs(spec, start, count)
    freq = runs.frequency(spec, start.date())
    dst = runs.dst_notes(spec, start.date()) if spec.tz else []
    facts = {"phrase": text, "timezone": zone_name, "start": start.isoformat(),
             "next_runs": [r.isoformat() for r in upcoming], "runs_per_day": freq.runs_per_day,
             "days_per_366": freq.days_per_year, "runs_per_366_days": freq.runs_per_year,
             "runs_per_week": _num(freq.runs_per_week), "gaps_minutes": list(freq.gaps),
             "months_never": [MONTH_NAMES[m - 1] for m in freq.months_never],
             "dst": [{"date": n.day.isoformat(), "kind": n.kind, "local": n.local} for n in dst or notes]}
    gotchas = ordered(_gotchas(spec, freq, dst, upcoming, probes))
    summary = f"Cron: {text.rstrip('.')} ({zone_name})."
    if upcoming:
        summary += f" Next: {_short(upcoming[0])}."
    return Analysis("cron", config, facts, tuple(gotchas), summary=summary, explanation=text)


def _with_tz(spec: CronSpec, tz: str) -> CronSpec:
    data = {name: getattr(spec, name) for name in spec.__dataclass_fields__}
    return CronSpec(**{**data, "tz": tz})


def _num(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


def _short(moment: datetime) -> str:
    return moment.strftime("%a %Y-%m-%d %H:%M")


def _c07() -> Gotcha:
    return Gotcha("C07", "info", "`@reboot` runs once when the cron daemon starts, not at a time of day; "
                  "many hosted schedulers (Kubernetes, GitHub Actions, AWS) don't support it.", "@reboot")


def _gotchas(spec: CronSpec, freq: runs.Frequency, dst: list, upcoming: list, probes: Probes) -> list[Gotcha]:
    found: list[Gotcha] = []
    minute, hour, dom, month, dow = spec.fields[:5]
    if spec.or_rule:
        found.append(Gotcha("C01", "medium", "When both day-of-month and day-of-week are set, cron runs on a day "
                            f"that matches EITHER one: this runs on day-of-month `{dom}` and also on every "
                            f"day-of-week `{dow}`.", f"{dom} … {dow}"))
    for value, size, unit in ((minute, 60, "minute"), (hour, 24, "hour")):
        if "/" in value and size % int(value.split("/")[1]) and len(freq.gaps) > 1:
            gaps = ", ".join(str(g) for g in freq.gaps)
            found.append(Gotcha("C02", "low", f"The step `{value}` doesn't divide {size} {unit}s evenly, so the "
                                f"gaps between runs are not equal: {gaps} minutes.", value))
    if freq.months_never and len(freq.months_never) < len(spec.months):
        names = ", ".join(MONTH_NAMES[m - 1] for m in freq.months_never)
        found.append(Gotcha("C03", "medium", f"Day-of-month `{dom}` doesn't exist in every month: it never runs "
                            f"in {names}.", dom))
    if spec.months == frozenset({2}) and not spec.dom_star and min(spec.doms) == 29 and not spec.or_rule:
        when = upcoming[0].date().isoformat() if upcoming else "not within 8 years"
        found.append(Gotcha("C04", "medium", f"29 February exists only in leap years: the next run is {when}.",
                            f"{dom} {month}"))
    if len(spec.minutes) == 60 and len(spec.hours) == 24:
        found.append(Gotcha("C05", "low", "This runs every minute (1,440 times a day on each matching day).",
                            f"{minute} {hour}"))
    if not spec.tz:
        text = "No time zone is set: the times depend on the scheduler's zone; I computed them in UTC."
        if _GITHUB.search(probes.words):
            text += " GitHub Actions schedules always run in UTC and may start late under load."
        found.append(Gotcha("C06", "info", text, spec.raw))
    for note in dst:
        verb = "is skipped" if note.kind == "skipped" else "happens twice, so it may run twice"
        found.append(Gotcha("C08", "medium", f"Daylight saving: {note.local} on {note.day.isoformat()} {verb} in "
                            f"{spec.tz}; cron implementations differ at transitions.", note.day.isoformat()))
    for value, unit in zip(spec.fields[:5], ("minute", "hour", "day-of-month", "month", "day-of-week"), strict=True):
        if value.endswith("/1") or value == FULL[unit]:
            found.append(Gotcha("C09", "info", f"`{value}` in the {unit} field is the same as `*`.", value))
    dialect = _OTHER_DIALECT.search(probes.words)
    if dialect and not spec.aws:
        found.append(Gotcha("C10", "low", f"You mentioned {dialect.group(1)}: those formats add a seconds field "
                            "(Quartz/Spring) or a year field with `?` (AWS), so the same five values mean "
                            "different fields there. I read this as standard 5-field cron.", spec.raw))
    return found


def utc_now() -> datetime:
    return datetime.now(UTC)
