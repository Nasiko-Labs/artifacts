"""Pure: the markdown reply, the envelope and the json-mode block, all rendered in code from the checked report.
URLs only from tool output; no HTML; every number formatted here."""

from __future__ import annotations

import json
import math
from decimal import ROUND_HALF_UP, Decimal

from accounts import Behind
from detect import COUNT_METRICS, LABELS, SHORT, Anomaly
from report import DayCheck, Flag, Report

_CENT = Decimal("0.01")
_VALUE_ORDER = ("new_mrr", "churned_mrr", "expansion_mrr", "contraction_mrr", "net_new_mrr", "failed_count",
                "failed_amount")


def money(minor: float) -> str:
    amount = (Decimal(str(minor)) / 100).quantize(_CENT, rounding=ROUND_HALF_UP)
    return f"{amount:,.2f}"


def signed_money(minor: int) -> str:
    return ("+" if minor > 0 else "") + money(minor)


def z_text(z: float) -> str:
    if math.isinf(z):
        return "inf" if z > 0 else "-inf"
    return f"{Decimal(str(z)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}"


def value_text(metric: str, value: float) -> str:
    if metric in COUNT_METRICS:
        number = Decimal(str(value))
        return f"{number.normalize():f}" if number != number.to_integral_value() else str(int(number))
    return signed_money(int(value)) if metric == "net_new_mrr" else money(value)


def mode_label(report: Report) -> str:
    if report.source == "pasted":
        return "pasted rows"
    return {False: "test mode", True: "live mode"}.get(report.livemode, "")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word.replace('anomaly', 'anomalies')}"


def day_header(check: DayCheck, report: Report) -> str:
    mode = ", ".join(p for p in (mode_label(report), check.currency) if p)
    lead = f"Revenue check for {check.day} ({mode})"
    if not check.enough_history:
        return (f"{lead}: not enough history ({check.history_days} of 14 baseline days), so no anomaly is "
                "asserted")
    return f"{lead}: {_plural(len(check.flags), 'anomaly') if check.flags else 'no anomalies'}"


def accounts_text(behind: Behind, report: Report, with_facts: bool = True) -> str:
    if report.source == "pasted":
        return "accounts: not available"
    if not behind.accounts:
        return "no account named"
    parts = []
    for a in behind.accounts:
        detail = f"{a.share_of_move}%, {a.fact}" if with_facts else f"{a.share_of_move}%"
        parts.append(f"{a.customer} {money(a.contribution)} ({detail})")
    if behind.more_count:
        parts.append(f"{behind.more_count} more {money(behind.more_total)}")
    text = ", ".join(parts)
    return text + ". Concentrated in one account" if behind.concentrated else text


def flag_line(flag: Flag, report: Report, with_facts: bool = True) -> str:
    a: Anomaly = flag.anomaly
    head = (f"{LABELS[a.metric]} {value_text(a.metric, a.value)} vs median {value_text(a.metric, a.median)} "
            f"(z {z_text(a.z)}, {a.severity}, {a.good_or_bad})")
    return f"{head}: {accounts_text(flag.behind, report, with_facts)}"


def clear_text(metrics: tuple[str, ...]) -> str:
    return ", ".join(dict.fromkeys(SHORT[m] for m in metrics))


def day_values(check: DayCheck) -> str:
    return ", ".join(f"{SHORT[k]} {value_text(k, check.values.get(k, 0))}" for k in _VALUE_ORDER
                     if k not in check.not_computed)


def check_block(check: DayCheck, report: Report) -> list[str]:
    lines = [day_header(check, report)]
    if not check.enough_history:
        return [*lines, f"- The day: {day_values(check)}"]
    lines += [f"- {flag_line(f, report)}" for f in check.flags]
    if check.clear:
        lines.append(f"- No anomaly in {clear_text(check.clear)}")
    if check.not_computed:
        lines.append(f"- Not computed: {clear_text(check.not_computed)} (events not read)")
    return lines


def markdown(report: Report, post_hint: bool = True) -> str:
    blocks = ["\n".join(check_block(c, report)) for c in report.checks]
    tail = [*report.notes]
    if report.extra.get("bad_rows"):
        tail.append(f"{report.extra['bad_rows']} pasted row(s) could not be read and were left out.")
    if post_hint and report.post:
        where = report.channel or "#revenue"
        tail.append(f'Chat post ready. Say "post it to {where}".')
    return "\n\n".join([*blocks, *tail])


def summary(report: Report) -> str:
    flags = report.flags
    days = sorted({c.day for c in report.checks})
    span = days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"
    if not flags:
        return f"Revenue check for {span}: no anomalies."
    return f"Revenue check for {span}: {_plural(len(flags), 'anomaly')}."


def _anomaly_json(a: Anomaly) -> dict:
    return {"day": a.day, "currency": a.currency, "metric": a.metric, "direction": a.direction,
            "value": _number(a.metric, a.value), "median": _number(a.metric, a.median), "z": z_text(a.z),
            "severity": a.severity, "good_or_bad": a.good_or_bad}


def _number(metric: str, value: float) -> float | int:
    if metric in COUNT_METRICS:
        return value
    return float((Decimal(str(value)) / 100).quantize(_CENT, rounding=ROUND_HALF_UP))


def envelope(report: Report) -> dict:
    accounts = [{"customer": a.customer, "contribution": float(Decimal(a.contribution) / 100),
                 "share_of_move": float(a.share_of_move), "fact": a.fact, "link": a.link or None,
                 "metric": f.anomaly.metric, "day": f.anomaly.day}
                for f in report.flags for a in f.behind.accounts] if report.source != "pasted" else []
    anomalies = [_anomaly_json(f.anomaly) for f in report.flags]
    days = [{"day": c.day, "currency": c.currency, "history_days": c.history_days,
             "enough_history": c.enough_history,
             "metrics": {k: _number(k, v) for k, v in c.values.items() if k not in c.not_computed}}
            for c in report.checks]
    return {"summary": summary(report), "items": accounts, "confidence": report.confidence,
            "needs_human": report.needs_human, "anomaly": anomalies[0] if anomalies else None,
            "anomalies": anomalies, "accounts": accounts, "slack_post": report.post or None,
            "days": days, "baseline_days": report.baseline_days, "mode": mode_label(report) or None,
            "notes": list(report.notes)}


def json_block(env: dict) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"


def copyable(target: str, body: str, reason: str) -> str:
    return f"{reason}\n\nHere is the post for {target} to copy:\n\n" + "\n".join(f"> {x}" if x else ">"
                                                                             for x in body.split("\n"))
