"""Pure: what of the model's answer survives. Every number and date in model text must be one of the digest's
strings; a cause starts with "Hypothesis:" (code adds it) and cites a known evidence id; a recommendation
cites an evidence id and states its assumption. Failing items are dropped and counted; a failing narrative
is replaced by the code template."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import digest as dg

NARRATIVE_MAX_WORDS = 150
FIELD_MAX_CHARS = 600
SMALL_INT_MAX = 10
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_NUMBER = re.compile(r"[+\-−]?[$€£]?\d[\d,]*(?:\.\d+)?\s?(?:%|σ|[kKmM]\b)?")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_HTML = re.compile(r"<[^>]{1,500}>")
_URL = re.compile(r"https?://\S+")
_PREFIX = re.compile(r"^\s*hypothesis\s*[:\-–]\s*", re.IGNORECASE)


def norm_number(token: str) -> str:
    t = token.replace("−", "-").replace(" ", "").lstrip("+-")
    t = re.sub(r"[$€£,]", "", t)
    return t.casefold().rstrip("σ")


def allowed_numbers(digest: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    texts = dg.strings(digest)
    numbers = {norm_number(m.group(0)) for s in texts for m in _NUMBER.finditer(_DATE.sub(" ", s))}
    dates = {m.group(0) for s in texts for m in _DATE.finditer(s)}
    return numbers | {n.rstrip("%") for n in numbers}, dates


def ungrounded(text: str, numbers: set[str], dates: set[str]) -> list[str]:
    bad = [d for d in _DATE.findall(text) if d not in dates and not any(x.startswith(d) for x in dates)]
    for match in _NUMBER.finditer(_DATE.sub(" ", text)):
        token = match.group(0).strip()
        n = norm_number(token)
        if n in numbers:
            continue
        if re.fullmatch(r"\d+", n) and int(n) <= SMALL_INT_MAX and not re.search(r"[$€£%]", token):
            continue
        bad.append(token)
    return bad


def clean_text(text: Any, source: str = "") -> str:
    """No images, no raw HTML, links only when the URL is in the input."""
    out = _IMAGE.sub("", str(text or ""))
    out = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), out)
    out = _URL.sub(lambda m: m.group(0) if m.group(0) in source else "", out)
    return " ".join(_HTML.sub("", out).split())[:FIELD_MAX_CHARS]


@dataclass
class Checked:
    narrative: str | None
    causes: dict[str, dict] = field(default_factory=dict)       # mover name → {likely_cause, evidence_ids}
    recommendations: list[dict] = field(default_factory=list)
    dropped: int = 0


def check(parsed: Mapping[str, Any], digest: Mapping[str, Any], source: str = "") -> Checked:
    numbers, dates = allowed_numbers(digest)
    ids = dg.evidence_ids(digest)
    names = {m["name"] for m in digest.get("top_movers", [])}
    narrative = clean_text(parsed.get("narrative"), source)
    ok = narrative and len(narrative.split()) <= NARRATIVE_MAX_WORDS and not ungrounded(narrative, numbers, dates)
    out = Checked(narrative if ok else None)
    for item in parsed.get("movers") or []:
        cause = _cause(item, names, ids, numbers, dates, source)
        if cause is None:
            out.dropped += 1
        elif cause[0] not in out.causes:
            out.causes[cause[0]] = cause[1]
    for n, item in enumerate(parsed.get("recommendations") or [], start=1):
        rec = _recommendation(item, ids, numbers, dates, source, n)
        if rec is None:
            out.dropped += 1
        else:
            out.recommendations.append(rec)
    return out


def _cited(item: Mapping[str, Any], ids: set[str]) -> list[str]:
    raw = item.get("evidence_ids") if isinstance(item, Mapping) else None
    return [str(i) for i in raw if str(i) in ids] if isinstance(raw, Sequence) and not isinstance(raw, str) else []


def _cause(item: Any, names: set[str], ids: set[str], numbers: set[str], dates: set[str], source: str):
    if not isinstance(item, Mapping) or str(item.get("name") or "") not in names:
        return None
    text = _PREFIX.sub("", clean_text(item.get("likely_cause"), source))
    cited = _cited(item, ids)
    if not text or not cited or ungrounded(text, numbers, dates):
        return None
    return str(item["name"]), {"likely_cause": f"Hypothesis: {text}", "evidence_ids": cited}


def _recommendation(item: Any, ids: set[str], numbers: set[str], dates: set[str], source: str, n: int):
    if not isinstance(item, Mapping):
        return None
    text, assumption = clean_text(item.get("text"), source), clean_text(item.get("assumption"), source)
    cited = _cited(item, ids)
    if not text or not assumption or not cited or ungrounded(f"{text} {assumption}", numbers, dates):
        return None
    return {"id": f"r{n}", "text": text, "est_monthly": None, "assumption": assumption, "evidence_ids": cited}


def template_narrative(result: Mapping[str, Any], digest: Mapping[str, Any]) -> str:
    t = digest["totals"]
    parts = [f"Over {digest['period']}, gross spend was {t['gross']} (net {t['net']}, {t['daily_avg']}/day)."]
    movers = digest["top_movers"][:3]
    if movers:
        listed = "; ".join(f"{m['name']} {m['change']} ({m['change_pct']})" for m in movers)
        parts.append(f"Biggest movers ({digest['comparison']}): {listed}.")
    elif digest["comparison"]:
        parts.append(f"No comparison: {digest['comparison']}")
    if digest["anomalies"]:
        a = digest["anomalies"][0]
        parts.append(f"Largest anomaly: {a['service']} on {a['dates']} at {a['value']} vs a trailing mean of "
                     f"{a['trailing_mean']} ({a['sigma']}).")
    u = digest.get("untagged")
    if isinstance(u, Mapping):
        parts.append(f"Untagged ({u['tag']}): {u['share']} of gross spend.")
    return " ".join(parts)
