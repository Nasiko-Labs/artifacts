"""Pure: what of the model's answer survives. Every number and date in model text must appear in the digest's
strings; a recommendation must cite a known finding id and state its assumption. Failing items are dropped and
counted; a failing summary is replaced by the code template. Text is cleaned of images, raw HTML and links that
are not in the input."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import digest as dg

SUMMARY_MAX_WORDS = 70
FIELD_MAX_CHARS = 500
SMALL_INT_MAX = 10
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_NUMBER = re.compile(r"[+\-−]?[$€£]?\d[\d,]*(?:\.\d+)?\s?(?:%|[kKmM]\b)?")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_HTML = re.compile(r"<[^>]{1,500}>")
_URL = re.compile(r"https?://\S+")


def norm_number(token: str) -> str:
    t = token.replace("−", "-").replace(" ", "").lstrip("+-")
    return re.sub(r"[$€£,]", "", t).casefold()


def allowed(d: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    texts = dg.strings(d)
    numbers = {norm_number(m.group(0)) for s in texts for m in _NUMBER.finditer(_DATE.sub(" ", s))}
    dates = {m.group(0) for s in texts for m in _DATE.finditer(s)}
    return numbers | {n.rstrip("%") for n in numbers}, dates


def ungrounded(text: str, numbers: set[str], dates: set[str]) -> list[str]:
    bad = [d for d in _DATE.findall(text) if d not in dates]
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
    out = _IMAGE.sub("", str(text or ""))
    out = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), out)
    out = _URL.sub(lambda m: m.group(0) if m.group(0) in source else "", out)
    return " ".join(_HTML.sub("", out).split())[:FIELD_MAX_CHARS]


@dataclass
class Checked:
    summary: str | None
    recommendations: list[dict] = field(default_factory=list)
    dropped: int = 0


def check(parsed: Mapping[str, Any], d: Mapping[str, Any], source: str = "") -> Checked:
    numbers, dates = allowed(d)
    ids = dg.finding_ids(d)
    summary = clean_text(parsed.get("summary"), source)
    ok = bool(summary) and len(summary.split()) <= SUMMARY_MAX_WORDS and not ungrounded(summary, numbers, dates)
    out = Checked(summary if ok else None)
    seen: set[str] = set()
    for item in parsed.get("recommendations") or []:
        rec = _recommendation(item, ids, numbers, dates, source)
        if rec is None or rec["finding_id"] in seen:
            out.dropped += 1
            continue
        seen.add(rec["finding_id"])
        out.recommendations.append(rec)
    return out


def _recommendation(item: Any, ids: set[str], numbers: set[str], dates: set[str], source: str) -> dict | None:
    if not isinstance(item, Mapping):
        return None
    fid = str(item.get("finding_id") or "").strip()
    text, assumption = clean_text(item.get("text"), source), clean_text(item.get("assumption"), source)
    if fid not in ids or not text or not assumption or ungrounded(f"{text} {assumption}", numbers, dates):
        return None
    return {"finding_id": fid, "text": text, "assumption": assumption, "source": "model"}


def template_recommendations(findings: list[dict]) -> list[dict]:
    return [{"finding_id": f["id"], "text": f["hint"], "assumption": f["assumption"], "source": "template"}
            for f in findings[:dg.MAX_FINDINGS]]


def template_summary(d: Mapping[str, Any], team_label: str) -> str:
    t = d["totals"]
    parts = [f"{d['period']}: {t['cost']} over {t['requests']} requests and {t['tokens']} tokens."]
    for name, label in (("by_model", "model"), (f"by_{team_label}", team_label)):
        rows = d.get(name) or []
        if rows:
            parts.append(f"Top {label}: {rows[0]['key']} ({rows[0]['share']}).")
    findings = d["findings"]
    if findings:
        parts.append(f"{len(findings)} waste finding{'s' if len(findings) != 1 else ''}; largest: "
                     f"{findings[0]['title']} ({findings[0]['estimate']}).")
    else:
        parts.append("No waste rule fired.")
    return " ".join(parts)
