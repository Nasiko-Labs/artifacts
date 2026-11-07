"""Pure: the reply, rendered in code from the computed report: markdown for people, and the envelope
(`summary, items, confidence, needs_human, noisy_alerts, proposals`) for API clients and workflow steps.
Names are shown in code spans (never raw HTML); links only when a tool returned them."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from propose import fmt

_URL = re.compile(r"^https?://\S+$")
_DIGIT = re.compile(r"\d")


def code(text: str) -> str:
    return "`" + (text or "unknown").replace("`", "'").replace("\n", " ") + "`"


def evidence_line(evidence: Sequence[Mapping]) -> str:
    out = []
    for e in evidence:
        number = str(e.get("number") or "").replace("[", "").replace("]", "")
        url = str(e.get("url") or "")
        out.append(f"[{number}]({url})" if _URL.match(url) else number)
    return ", ".join(out) or "none"


def minutes(value: float | None) -> str:
    return "unknown" if value is None else f"{fmt(value)} min"


def alert_line(i: int, s: Mapping) -> str:
    checked = (f"{s['zero_action']} zero-action ({s['zero_action_pct']}%)" if s["assessed"] == s["fired"]
               else f"{s['zero_action']} of {s['assessed']} checked zero-action ({s['zero_action_pct']}%)")
    service = f" (service {code(s['service'])})" if s.get("service") else ""
    return (f"{i}. {code(s['alert'])}{service} fired {s['fired']}x ({fmt(s['fired_per_week'])}/week), {checked}, "
            f"median {minutes(s['median_duration_min'])}, p90 {minutes(s['p90_duration_min'])}, {s['ack_count']} acks")


def proposal_line(p: Mapping) -> str:
    text = f"   - Proposal ({p['kind']}): {p['change']}"
    if p.get("avoided") is not None and p.get("of"):
        noun = "values" if p["kind"] == "threshold_change" else "zero-action firings"
        text += f" -> would have suppressed {p['avoided']} of {p['of']} {noun}"
    if p.get("file"):
        text += f" · definition: {code(str(p['file']) + ':' + str(p['line']))}"
    elif p.get("note"):
        text += f" · {p['note']}"
    return text


def markdown(report: Mapping) -> str:
    params = report["params"]
    noisy = report["noisy"]
    head = (f"**Noisy alerts · last {params['window_days']} d · fired >= {params['min_firings']}, >= 80% zero-action · "
            f"{len(noisy)} found of {report['total_alerts']} alerts**")
    lines = [head]
    if params.get("service"):
        lines.append(f"Service: {code(params['service'])}")
    if report.get("source") == "pasted":
        lines.append("Source: the pasted incident export (low confidence: no live logs).")
    if report.get("headline"):
        lines += ["", report["headline"]]
    lines.append("")
    for i, s in enumerate(noisy, start=1):
        lines.append(alert_line(i, s))
        lines += [proposal_line(p) for p in report["proposals"] if p["alert"] == s["alert"]]
        lines.append(f"   - Evidence: {evidence_line(s['evidence'])}")
    lines += _tail(report)
    return "\n".join(lines).strip()


def _tail(report: Mapping) -> list[str]:
    out = []
    chatty = report.get("chatty") or []
    if chatty:
        listed = "; ".join(f"{code(s['alert'])} ({s['fired']} fired, {s['assessed'] - s['zero_action']} actioned)"
                           for s in chatty)
        out += ["", f"Chatty but actioned: {listed} - not proposed."]
    if report.get("too_unknown"):
        out += ["", "Not judged (more than 20% of their logs unreadable): "
                + ", ".join(code(n) for n in report["too_unknown"]) + "."]
    if report.get("coverage"):
        out += ["", "Coverage:"] + [f"- {c}" for c in report["coverage"]]
    if any(p["kind"] == "lengthen_for" for p in report["proposals"]):
        out += ["", ('Say "open the PR for #1 in owner/repo" to change a `for:` line (one approval per write), or '
                     '"show incidents for <alert>".')]
    return out


def no_noisy(report: Mapping, text: str) -> str:
    lines = [text]
    lines += _tail({**report, "proposals": []})
    return "\n".join(lines).strip()


def confidence(report: Mapping) -> str:
    if report.get("source") == "pasted":
        return "low"
    gaps = report.get("coverage") or report.get("too_unknown") or any(s["unknown"] for s in report["noisy"])
    missing_defs = any(p.get("note") for p in report["proposals"])
    return "medium" if gaps or missing_defs else "high"


def needs_human(report: Mapping) -> bool:
    return any(p["kind"] == "review_or_remove" or p.get("ambiguous") for p in report["proposals"])


def envelope(report: Mapping) -> dict:
    noisy = [{k: v for k, v in s.items() if k != "incidents"} for s in report["noisy"]]
    proposals = [{k: v for k, v in p.items() if k not in ("note", "ambiguous")} for p in report["proposals"]]
    params = report["params"]
    return {"summary": report.get("headline") or summary(report), "items": noisy, "confidence": confidence(report),
            "needs_human": needs_human(report), "noisy_alerts": noisy, "proposals": proposals,
            "window_days": params["window_days"], "min_firings": params["min_firings"],
            "service": params.get("service") or None, "repo": report.get("repo") or None}


def summary(report: Mapping) -> str:
    noisy = report["noisy"]
    if not noisy:
        return "No noisy alerts in the window."
    names = ", ".join(code(s["alert"]) for s in noisy[:3])
    return f"Noisiest alerts: {names}. Proposals are computed from how fast they self-resolve."


def as_json(env: Mapping) -> str:
    return "```json\n" + json.dumps(env, indent=2, default=str) + "\n```"


def headline_ok(text: str) -> bool:
    """At most two sentences, no digit (numbers come from code only), no markup."""
    clean = (text or "").strip()
    sentences = [s for s in re.split(r"[.!?](?:\s|$)", clean) if s.strip()]
    return bool(clean) and len(clean) <= 300 and len(sentences) <= 2 and not _DIGIT.search(clean) and "<" not in clean


def facts_for_headline(report: Mapping) -> str:
    lines = [f"alert: {s['alert']}; service: {s.get('service') or 'unknown'}" for s in report["noisy"][:5]]
    kinds = sorted({p["kind"] for p in report["proposals"]})
    return "\n".join([*lines, f"proposal kinds: {', '.join(kinds) or 'none'}"])
