"""Pure: the chat post. Code assembles it; the model words only the headline sentence, which is checked here.

Format: headline, up to 3 anomaly lines (metric, value vs median, severity, accounts), one "No anomaly in: ..."
line; at most 900 characters. Every number in the headline (and in any edited text) must be one the report shows
(the allow-list); a cause or forecast word, a link, or a second line rejects the headline and the code headline is
used instead. Account names come only from the checked report."""

from __future__ import annotations

import json
import re

import render
from detect import LABELS
from report import Flag, Report

MAX_CHARS = 900
MAX_HEADLINE_CHARS = 200
MAX_FLAG_LINES = 3
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_CAUSAL = re.compile(r"\b(?:because|due to|driven by|thanks to|as a result|caused by|owing to|led by|attributed|"
                     r"pricing change|will|expect\w*|forecast\w*|likely|probably)\b", re.IGNORECASE)
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)
_SEVERITY_RANK = {"high": 0, "medium": 1}


def ranked_flags(report: Report) -> tuple[Flag, ...]:
    return tuple(sorted(report.flags, key=lambda f: (_SEVERITY_RANK.get(f.anomaly.severity, 2), -abs(f.anomaly.z))))


def span(report: Report) -> str:
    days = sorted({c.day for c in report.checks})
    return days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"


def code_headline(report: Report) -> str:
    mode = render.mode_label(report)
    lead = f"Revenue check for {span(report)}" + (f" ({mode})" if mode else "")
    n = len(report.flags)
    if not n:
        return f"{lead}: no anomalies."
    return f"{lead}: {n} {'anomaly' if n == 1 else 'anomalies'} flagged."


def facts(report: Report) -> dict:
    """The exact values the model words the headline from."""
    return {"days": span(report), "mode": render.mode_label(report) or None,
            "anomalies": [{"metric": LABELS[f.anomaly.metric], "day": f.anomaly.day,
                           "currency": f.anomaly.currency, "value": render.value_text(f.anomaly.metric,
                                                                                       f.anomaly.value),
                           "median": render.value_text(f.anomaly.metric, f.anomaly.median),
                           "z": render.z_text(f.anomaly.z), "severity": f.anomaly.severity,
                           "direction": f.anomaly.direction, "good_or_bad": f.anomaly.good_or_bad}
                          for f in ranked_flags(report)],
            "no_anomaly_in": _clear(report)}


def _clear(report: Report) -> str:
    """Metrics clear on every checked day with enough history."""
    checked = [c for c in report.checks if c.enough_history]
    if not checked:
        return ""
    common = [m for m in checked[0].clear if all(m in c.clear for c in checked)]
    return render.clear_text(tuple(common))


def body_lines(report: Report) -> list[str]:
    flags = ranked_flags(report)
    many_days = len({c.day for c in report.checks}) > 1
    lines = []
    for f in flags[:MAX_FLAG_LINES]:
        prefix = f"{f.anomaly.day} " if many_days else ""
        lines.append(f"- {prefix}{render.flag_line(f, report, with_facts=False)}")
    if len(flags) > MAX_FLAG_LINES:
        lines.append(f"- and {len(flags) - MAX_FLAG_LINES} more flagged")
    clear = _clear(report)
    if clear:
        lines.append(f"- No anomaly in: {clear}")
    return lines


def assemble(headline: str, report: Report) -> str:
    lines = body_lines(report)
    text = "\n".join([headline, *lines])
    while len(text) > MAX_CHARS and lines:  # drop the least important line until it fits
        lines = lines[:-1]
        text = "\n".join([headline, *lines])
    return text[:MAX_CHARS]


def allowed_numbers(report: Report) -> frozenset[str]:
    """Every number token the rendered reply, the post lines and the facts contain, in all its spellings."""
    texts = [render.markdown(report, post_hint=False), "\n".join(body_lines(report)), code_headline(report),
             json.dumps(facts(report))]
    tokens = {t for text in texts for t in _NUMBER.findall(text)}
    return frozenset({v for t in tokens for v in _variants(t)})


def _variants(token: str) -> set[str]:
    bare = token.lstrip("+-")
    plain = bare.replace(",", "")
    out = {token, bare, plain}
    if "." in plain:
        out |= {plain.rstrip("0").rstrip("."), bare.rstrip("0").rstrip(".")}
    return out


def stray_numbers(text: str, allowed: frozenset[str]) -> list[str]:
    return [t for t in _NUMBER.findall(text or "")
            if t.lstrip("+-").rstrip(".,") not in allowed and t.lstrip("+-").replace(",", "") not in allowed]


def headline_problem(text: str, report: Report) -> str | None:
    line = (text or "").strip().strip('"')
    if not line or "\n" in line or len(line) > MAX_HEADLINE_CHARS:
        return "empty, multi-line or too long"
    stray = stray_numbers(line, allowed_numbers(report))
    if stray:
        return f"numbers not in the check: {', '.join(stray[:3])}"
    if _CAUSAL.search(line):
        return "claims a cause or a forecast"
    if _LINK.search(line):
        return "carries a link"
    return None


def edit_problem(text: str, allowed: frozenset[str]) -> str | None:
    """Why an edited post cannot go out, or None. `allowed` is `allowed_numbers` of the check it was drafted from."""
    body = (text or "").strip()
    if not body:
        return "it is empty"
    if len(body) > MAX_CHARS:
        return f"it is longer than {MAX_CHARS} characters"
    stray = stray_numbers(body, allowed)
    if stray:
        return f"the numbers come from your payments account, and {', '.join(stray[:3])} is not one of them"
    if _CAUSAL.search(body):
        return "it states a cause or a forecast the data does not show"
    return None
