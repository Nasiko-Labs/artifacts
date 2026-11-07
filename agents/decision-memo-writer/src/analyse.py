"""The analyse step: one JSON call (plus at most one repair) for pros, cons, risks and per-criterion
ratings, then code checks: a contract violation (missing option, rating outside 1-5) is repaired; a
basis that is not verbatim becomes "judgement"; an item with an invented figure is dropped."""

from __future__ import annotations

import re
from typing import Any

import llm
import prompts
from brief import Brief
from criteria import MAX_RATING, MIN_RATING
from grounding import Ground
from text import first_words

ITEM_MAX_WORDS = 18
RISK_MAX_WORDS = 20
MAX_POINTS = 4
MAX_RISKS = 5
SEVERITIES = ("high", "medium", "low")


def view(brief: Brief) -> dict:
    """The brief as the model sees it: parsed parts plus the full text to quote from."""
    data = {"decision": brief.decision,
            "options": [{"key": o.key, "name": o.name, "text": o.text, "added_by_me": o.added_by_me}
                        for o in brief.options],
            "constraints": list(brief.constraints), "data": list(brief.data),
            "criteria": [c.name for c in brief.criteria], "audience": brief.audience or None,
            "full_brief": brief.raw}
    if brief.extra:
        data["added_data"] = list(brief.extra)
    return data


_KEY = re.compile(r"^\s*(?:option\s+)?([A-T])\b", re.IGNORECASE)


def option_key(value: object) -> str:
    """"B", "Option B", "b)", "B. Buy" -> "B"; anything else (a name, a sentence) -> ""."""
    m = _KEY.match(str(value or ""))
    return m.group(1).upper() if m and not re.match(r"^\s*[Aa]\s+\w", str(value)) else ""


def rating(value: object) -> int | None:
    """An integer rating 1-5 (a whole float or digit string counts), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    return value if isinstance(value, int) and MIN_RATING <= value <= MAX_RATING else None


def _rows(data: dict) -> dict[str, dict]:
    return {option_key(o.get("key")): o for o in data.get("options") or () if isinstance(o, dict)}


def validate(data: Any, brief: Brief) -> dict | None:
    """The answer when it meets the contract, else None (one repair follows)."""
    if not isinstance(data, dict) or data.get("status") not in ("ok", "off_topic"):
        return None
    if data["status"] == "off_topic":
        return {"status": "off_topic"}
    rows = _rows(data)
    if any(o.key not in rows for o in brief.options):
        return None
    for o in brief.options:
        ratings = {str(k).casefold().strip(): v for k, v in (rows[o.key].get("ratings") or {}).items()}
        for c in brief.criteria:
            if rating(ratings.get(c.name)) is None:
                return None
    return data


async def run(deps: llm.TurnDeps, brief: Brief, policy: tuple[str, ...]) -> dict:
    data = await llm.complete_json(deps, prompts.analyse_messages(view(brief), policy), prompts.ANALYSE_CONTRACT,
                                   lambda d: validate(d, brief))
    return data if data["status"] == "off_topic" else check(data, brief)


def _points(rows: Any, key: str, sign: str, ground: Ground, tally: list[int]) -> list[dict]:
    kept: list[dict] = []
    for row in (rows if isinstance(rows, list) else [])[:MAX_POINTS]:
        text = " ".join(str((row or {}).get("text") if isinstance(row, dict) else row or "").split())
        tally[1] += 1
        if not text or ground.ungrounded(text):
            tally[0] += 1
            continue
        basis = ground.basis(row.get("basis") if isinstance(row, dict) else "")
        kept.append({"id": f"{key}-{sign}{len(kept) + 1}", "text": first_words(text, ITEM_MAX_WORDS), "basis": basis})
    return kept


def check(data: dict, brief: Brief) -> dict:
    ground = Ground(brief)
    tally = [0, 0]  # dropped, total
    rows = _rows(data)
    pros, cons, ratings = {}, {}, {}
    for o in brief.options:
        row = rows[o.key]
        pros[o.key] = _points(row.get("pros"), o.key, "pro", ground, tally)
        cons[o.key] = _points(row.get("cons"), o.key, "con", ground, tally)
        given = {str(k).casefold().strip(): v for k, v in (row.get("ratings") or {}).items()}
        ratings[o.key] = {c.name: rating(given[c.name]) for c in brief.criteria}
    risks = _risks(data.get("risks"), brief, ground, tally)
    return {"status": "ok", "pros": pros, "cons": cons, "ratings": ratings, "risks": risks,
            "dropped": tally[0], "total": tally[1]}


def _risks(rows: Any, brief: Brief, ground: Ground, tally: list[int]) -> list[dict]:
    keys = {o.key for o in brief.options}
    kept = []
    for row in (rows if isinstance(rows, list) else [])[:MAX_RISKS]:
        if not isinstance(row, dict):
            continue
        tally[1] += 1
        text = " ".join(str(row.get("text") or "").split())
        mitigation = " ".join(str(row.get("mitigation") or "").split()) or "[mitigation?]"
        if not text or ground.ungrounded(text) or ground.ungrounded(mitigation):
            tally[0] += 1
            continue
        option = option_key(row.get("option"))
        severity = str(row.get("severity") or "").casefold()
        kept.append({"option": option if option in keys else "", "text": first_words(text, RISK_MAX_WORDS),
                     "mitigation": first_words(mitigation, RISK_MAX_WORDS),
                     "severity": severity if severity in SEVERITIES else "medium",
                     "basis": ground.basis(row.get("basis"))})
    return kept


def model_view(analysis: dict) -> dict:
    """The checked analysis as the write step sees it (ids, texts, ratings)."""
    return {"pros": analysis["pros"], "cons": analysis["cons"], "ratings": analysis["ratings"],
            "risks": analysis["risks"]}
