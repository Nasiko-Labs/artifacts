"""Pure: the reply, rendered in code from computed values. Markdown only, no raw HTML or images; links only from
tool output (already verified verbatim). Service names come from the user's data and are escaped."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

import burn
import rank
from burn import Forecast
from models import Related, Run

_UNSAFE = re.compile(r"[<>`*_\[\]|\\]")
_LABEL = {burn.EXHAUSTED: "EXHAUSTED", burn.FAST: "FAST BURN", burn.SLOW: "SLOW BURN", burn.ELEVATED: "elevated",
          burn.HEALTHY: "healthy", burn.INSUFFICIENT: "insufficient data"}


def safe(text: str) -> str:
    return _UNSAFE.sub("", str(text))[:120]


def when(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


def day_time(ts: int) -> str:
    moment = datetime.fromtimestamp(ts, UTC)
    return f"{moment:%a} {moment.day} {moment:%b} {moment:%H:%M} UTC"


def clock(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M")


def x(value: Decimal | None) -> str:
    return "no traffic" if value is None else f"{burn.round_to(value, 2)}x"


def hours(value: Decimal) -> str:
    return f"{burn.round_to(value, 1)} h"


def pct(value: Decimal) -> str:
    return f"{burn.round_to(value * 100, 0):f}%"


def label(status: str) -> str:
    return _LABEL[status]


def budget_text(f: Forecast) -> str:
    if f.remaining_frac is None:
        return "budget unknown"
    if f.remaining_frac <= 0:
        return "budget left 0% (overspent)"
    return f"budget left {pct(f.remaining_frac)}"


def exhaust_text(f: Forecast) -> str:
    if f.status == burn.INSUFFICIENT:
        return f"no forecast: only {hours(f.span_h)} of data (need at least {burn.MIN_DATA_HOURS} h)"
    if "no_recent_traffic" in f.flags:
        return "no forecast: no traffic in the last 6 h"
    if f.hours_to_exhaust is None:
        return "not exhausting at this rate"
    if f.hours_to_exhaust <= 0:
        return "budget exhausted"
    if f.beyond_period:
        return "exhausts in more than one period"
    return f"exhausts in {hours(f.hours_to_exhaust)} ({day_time(f.exhaust_at)})"


def burn_text(f: Forecast) -> str:
    if f.status in (burn.HEALTHY, burn.INSUFFICIENT) and f.burn_current is not None:
        return f"burn {f.current_window} {x(f.burn_current)}"
    return f"burn 1h {x(f.burn['h1'])}, 6h {x(f.burn['h6'])}, 24h {x(f.burn['h24'])}"


def service_line(index: int, f: Forecast, share: int) -> str:
    head = f"{index}. **{safe(f.name)}** {f.slo.percent}% · {label(f.status)}"
    if f.status == burn.INSUFFICIENT:
        return f"{head} · {exhaust_text(f)}"
    parts = [head, burn_text(f), budget_text(f), exhaust_text(f)]
    if f.status in burn.NON_HEALTHY:
        parts.append(f"{share}% of errors")
        if f.worst_window:
            parts.append(f"worst window {clock(f.worst_window[0])}-{clock(f.worst_window[1])}")
    return " · ".join(parts)


def header(forecasts: Sequence[Forecast]) -> str:
    latest = max(forecasts, key=lambda f: f.as_of)
    periods = sorted({f.slo.period_days for f in forecasts})
    period = f"rolling {periods[0]} d" if len(periods) == 1 else "rolling periods per service"
    return f"**SLO burn** · as of {when(latest.as_of)} (data {hours(latest.data_age_h)} old) · {period}"


def related_lines(run: Run, names: Sequence[str]) -> list[str]:
    lines = []
    for name in names:
        for item in run.related.get(name, ()):
            lines.append(f"Possibly related ({safe(name)}): {related_item(item)}")
    return lines


def related_item(item: Related) -> str:
    title = safe(item.title)
    text = f"{item.kind} \"{title}\""
    if item.url:
        text = f"{item.kind} [{title}]({item.url})"
    return f"{text} {safe(item.when)}" if item.when else text


def basis(forecasts: Sequence[Forecast]) -> str:
    on_24 = [safe(f.name) for f in forecasts if f.current_window == "24h"]
    text = "Basis: current rate = 6 h window"
    if on_24:
        text += f" (24 h for {', '.join(on_24)}: under {burn.MIN_RECENT_EVENTS} requests in the last 6 h)"
    return text + "; steady traffic assumed, so hours are approximate; rolling period, as of each service's latest row."


def coverage_lines(forecasts: Sequence[Forecast], run: Run) -> list[str]:
    lines = _extrapolated_lines(forecasts)
    for f in forecasts:
        name = safe(f.name)
        if "stale" in f.flags:
            lines.append(f"{name}: data is stale ({hours(f.data_age_h)} old).")
        if "assumed_uniform_traffic" in f.flags:
            lines.append(f"{name}: no request counts, so traffic is assumed uniform across buckets.")
        if "looks_cumulative" in f.flags:
            lines.append(f"{name}: totals never decrease, so they look like running counters; if so, these numbers "
                         "are wrong. Send per-bucket counts.")
    if run.rejected:
        lines.append(f"{run.rejected} row(s) rejected (unreadable time, negative counts, errors above total, or "
                     "an error rate above 1).")
    if run.aggregated:
        lines.append("The paste was very large, so rows were summed into hourly buckets first.")
    return lines + list(run.related_notes)


def _extrapolated_lines(forecasts: Sequence[Forecast]) -> list[str]:
    """One line per (data span, period) pair, naming every service it applies to."""
    groups: dict[tuple[Decimal, int], list[str]] = {}
    for f in forecasts:
        if "extrapolated" in f.flags and f.status != burn.INSUFFICIENT:
            groups.setdefault((burn.round_to(f.span_h / 24, 1), f.slo.period_days), []).append(safe(f.name))
    return [f"Period traffic extrapolated for {', '.join(names)} (data spans {span} of {days} days)."
            for (span, days), names in groups.items()]


def report(forecasts: Sequence[Forecast], run: Run, headline: str) -> str:
    shares = rank.shares(forecasts)
    lines = [header(forecasts), "", headline, ""]
    lines += [service_line(i, f, shares.get(f.name, 0)) for i, f in enumerate(forecasts, 1)]
    related = related_lines(run, [f.name for f in forecasts])
    if related:
        lines += ["", *related]
    lines += ["", basis(forecasts)]
    notes = coverage_lines(forecasts, run)
    if notes:
        lines += ["", "Coverage: " + " ".join(notes)]
    return "\n".join(lines)


def what_if(changed: Sequence[tuple[Forecast, Forecast]], factor: Decimal) -> str:
    lines = [f"**What if** the current burn is multiplied by {factor.normalize():f} (computed, not observed):"]
    for before, after in changed:
        name = safe(before.name)
        if after.burn_current is None or before.status == burn.INSUFFICIENT:
            lines.append(f"- {name}: no forecast to change ({exhaust_text(before)}).")
            continue
        lines.append(f"- {name}: burn {x(before.burn_current)} -> {x(after.burn_current)}; {exhaust_text(after)} "
                     f"(was: {exhaust_text(before)}).")
    return "\n".join(lines)


def numbers(forecasts: Sequence[Forecast]) -> str:
    blocks = []
    for f in forecasts:
        rows = ["| window | requests | errors | error rate | burn |", "|---|---|---|---|---|"]
        for key, title in (("h1", "1 h"), ("h6", "6 h"), ("h24", "24 h"), ("period", f"{f.slo.period_days} d")):
            total, errors = f.counts.get(key, (Decimal(0), Decimal(0)))
            rate = "n/a" if total <= 0 else f"{burn.round_to(errors / total * 100, 3)}%"
            rows.append(f"| {title} | {burn.round_to(total, 0)} | {burn.round_to(errors, 0)} | {rate} | "
                        f"{x(f.burn[key])} |")
        budget = burn.round_to((1 - f.slo.target) * 100, 3)
        blocks.append(f"**{safe(f.name)}** (SLO {f.slo.percent}% over {f.slo.period_days} d, error budget "
                      f"{budget}% of requests; {budget_text(f)}; {exhaust_text(f)})\n" + "\n".join(rows))
    return "\n\n".join(blocks)


def why(f: Forecast, run: Run) -> str:
    lines = [f"**{safe(f.name)}** · {label(f.status)} · {burn_text(f)} · {budget_text(f)} · {exhaust_text(f)}"]
    if f.worst_window:
        lines.append(f"Worst 6-hour window in the last 24 h: {when(f.worst_window[0])} to {clock(f.worst_window[1])}.")
    related = related_lines(run, [f.name])
    lines += related or ["No possibly related issues or incidents were found."]
    lines += list(run.related_notes)
    lines.append("These are possibly related, not a confirmed cause.")
    return "\n".join(lines)


def json_block(envelope: Mapping) -> str:
    return "```json\n" + json.dumps(envelope, indent=2) + "\n```"


def template_headline(forecasts: Sequence[Forecast]) -> str:
    top = rank.top_burners(forecasts, 1)
    if not top:
        ready = [f for f in forecasts if f.status != burn.INSUFFICIENT]
        if not ready:
            return "There is not enough data to forecast any service yet."
        if len(ready) == 1:
            return f"{safe(ready[0].name)} is within its error budget at the current rate."
        return "Every service is within its error budget at the current rate."
    f = top[0]
    return f"{safe(f.name)} is burning its error budget fastest ({label(f.status)}): {exhaust_text(f)}."
