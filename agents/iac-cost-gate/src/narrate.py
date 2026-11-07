"""The one model step: word the summary and one fix per violation from the digest. Code checks what comes
back: every number in the summary must be one of the digest's strings, a fix must name a known violation
id and add no money figure. A failing summary is replaced by the code template; failing fixes are dropped
and counted."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import llm
import prompts
from verdict import money

SUMMARY_MAX_WORDS = 70
FIX_MAX_CHARS = 300
TOP_LINES = 5
SMALL_INT_MAX = 10
_NUMBER = re.compile(r"[+\-]?\$?\d[\d,]*(?:\.\d+)?\s?%?")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_HTML = re.compile(r"<[^>]{1,500}>")
_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Narrated:
    status: str                     # ok | off_topic
    result: dict | None = None


def digest(result: Mapping[str, Any]) -> dict:
    limit = result["limit"]
    out = {
        "verdict": result["verdict"].upper(), "verdict_reasons": result["verdict_reasons"],
        "estimated_monthly": money(result["estimated_monthly"]), "limit": money(limit) if limit is not None else "none",
        "region": result["region"],
        "top_lines": [{"address": ln["address"], "monthly": money(ln["monthly"]), "detail": ln["detail"]}
                      for ln in result["lines"][:TOP_LINES] if ln["basis"] == "priced"],
        "violations": [{"id": v["id"], "rule_id": v["rule_id"], "address": v["address"], "title": v["title"],
                        "saving": money(v["est_monthly_saving"]) if v["est_monthly_saving"] else "none",
                        "hint": v["hint"]} for v in result["violations"]],
        "unpriced_count": str(result["counts"]["unpriced"]),
        "usage_dependent_count": str(result["counts"]["usage_dependent"]),
    }
    if result["delta"] is not None:
        out["before"] = money(result["estimated_monthly_before"])
        out["delta"] = money(result["delta"], signed=True)
    return out


def template(result: Mapping[str, Any]) -> str:
    d = digest(result)
    head = f"Verdict {d['verdict']}: estimated {d['estimated_monthly']}/month"
    head += f" against a limit of {d['limit']}." if result["limit"] is not None else " (no limit set)."
    if result["delta"] is not None:
        head += f" Change vs before: {d['delta']} (before {d['before']})."
    mediums = sum(1 for v in result["violations"] if v["severity"] == "medium")
    head += f" {len(result['violations'])} violations ({mediums} medium)."
    return head + f" {d['unpriced_count']} unpriced, {d['usage_dependent_count']} usage-dependent."


def _strings(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [str(value)]


def _norm(token: str) -> str:
    return token.replace(" ", "").replace(",", "").replace("$", "").lstrip("+-").rstrip("%").rstrip(".")


def ungrounded(text: str, d: Mapping[str, Any]) -> list[str]:
    """Number tokens in `text` not in the digest; a money figure must match a money figure there."""
    tokens = [m.group(0) for s in _strings(d) for m in _NUMBER.finditer(s)]
    allowed = {_norm(t) for t in tokens}
    money_allowed = {_norm(t) for t in tokens if "$" in t}
    bad = []
    for match in _NUMBER.finditer(text):
        token, n = match.group(0), _norm(match.group(0))
        if "$" in token:
            if n not in money_allowed:
                bad.append(token)
            continue
        if n in allowed or (n.isdigit() and int(n) <= SMALL_INT_MAX and "%" not in token):
            continue
        bad.append(token)
    return bad


def clean(text: Any, source: str = "") -> str:
    out = _IMAGE.sub("", str(text or ""))
    out = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), out)
    out = _URL.sub(lambda m: m.group(0) if m.group(0) in source else "", out)
    return " ".join(_HTML.sub("", out).split()).replace("|", "/")


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.casefold()).split())


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("ok", "off_topic"):
        return None
    if data["status"] == "off_topic":
        return {"status": "off_topic"}
    if not isinstance(data.get("summary"), str):
        return None
    fixes = data.get("fixes") if isinstance(data.get("fixes"), list) else []
    return {"status": "ok", "summary": data["summary"], "fixes": fixes}


def check(parsed: Mapping[str, Any], result: Mapping[str, Any], source: str = "") -> dict:
    d = digest(result)
    ids = {v["id"] for v in result["violations"]}
    titles = {v["id"]: _plain(v["title"]) for v in result["violations"]}
    summary = clean(parsed.get("summary"), source)
    good = summary and len(summary.split()) <= SUMMARY_MAX_WORDS and not ungrounded(summary, d)
    fixes, dropped = {}, 0
    for fix in parsed.get("fixes") or []:
        vid = str(fix.get("violation_id") or "") if isinstance(fix, Mapping) else ""
        text = clean(fix.get("text"), source)[:FIX_MAX_CHARS] if isinstance(fix, Mapping) else ""
        if vid not in ids or not text or "$" in text or ungrounded(text, d) or vid in fixes:
            dropped += 1
            continue
        if _plain(text) == titles[vid]:  # only restates the finding: the stored hint says more
            continue
        fixes[vid] = text
    out = {**result, "fixes": fixes, "dropped": result.get("dropped", 0) + dropped}
    if not good:
        return {**out, "narrative": template(result), "narrative_source": "template", "dropped": out["dropped"] + 1}
    return {**out, "narrative": summary, "narrative_source": "model"}


def with_template(result: Mapping[str, Any]) -> dict:
    return {**result, "narrative": template(result), "narrative_source": "template", "fixes": {}}


async def narrate(deps: llm.TurnDeps, result: Mapping[str, Any], request: str, context: Sequence[str] = (),
                  source: str = "") -> Narrated:
    try:
        parsed = await llm.complete_json(deps, prompts.narrate_messages(digest(result), request, context),
                                         prompts.NARRATE_CONTRACT, parse)
    except llm.LlmError:
        return Narrated("ok", with_template(result))
    if parsed["status"] == "off_topic":
        return Narrated("off_topic")
    return Narrated("ok", check(parsed, result, source))
