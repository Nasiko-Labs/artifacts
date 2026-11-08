"""The one model step of a fresh run: word the summary / why / risk from the code's facts. Every number in the
answer must be in the facts (allow-list), else the whole explanation is replaced by the code template."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import fmt
import llm
import prompts

FIELDS = ("summary", "why", "risk")
FIELD_MAX_CHARS = 500
_UNSAFE = re.compile(r"!\[|<[^>]{1,200}>|https?://|\]\(")


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("ok", "off_topic"):
        return None
    if data["status"] == "off_topic":
        return {"status": "off_topic"}
    if not all(isinstance(data.get(f), str) and data[f].strip() for f in FIELDS):
        return None
    return {"status": "ok", **{f: " ".join(data[f].split()) for f in FIELDS}}


def is_unsafe(text: str) -> bool:
    return bool(_UNSAFE.search(text))


def check(parsed: Mapping[str, Any], facts: Mapping[str, Any]) -> dict | None:
    allow = fmt.allowed(facts)
    out = {f: parsed[f] for f in FIELDS}
    for text in out.values():
        if len(text) > FIELD_MAX_CHARS or is_unsafe(text) or fmt.ungrounded(text, allow):
            return None
    return out


def _rec_line(r: Mapping[str, Any]) -> tuple[str, str, str]:
    scope = f"Scope {r['scope']}"
    if r["action"] == "buy_now":
        buy = (f"{scope}: buy a {r['term_years']}y commitment at {fmt.money(r['hourly_commit'])} $/hr, saving "
               f"about {fmt.money(r['monthly_saving'])}/month.")
        why = (f"{scope}: it would be used {fmt.pct(r['utilization'])} of the time against a break-even of "
               f"{fmt.pct(r['break_even_utilization'])}.")
        risk = (f"{scope}: if usage falls 20% it saves {fmt.money(r['downside_monthly_saving'])}/month at "
                f"{fmt.pct(r['downside_utilization'])} utilization (risk {r['risk']}).")
        return buy, why, risk
    if r["action"] == "needs_more_data":
        return f"{scope}: not enough data.", f"{scope}: {r['reason']}.", f"{scope}: nothing to size yet."
    return (f"{scope}: no new commitment.", f"{scope}: {r['reason']}.",
            f"{scope}: buying anyway would risk paying for unused commitment.")


def template(result: Mapping[str, Any]) -> dict:
    lines = [_rec_line(r) for r in result["recommendations"]]
    existing = " ".join(f"{e['id']}: {e['action'].replace('_', ' ')}." for e in result["existing"])
    summary = " ".join(x[0] for x in lines) + (f" Existing: {existing}" if existing else "")
    return {"summary": summary, "why": " ".join(x[1] for x in lines), "risk": " ".join(x[2] for x in lines)}


def with_template(result: Mapping[str, Any]) -> dict:
    return {**result, "explanation": template(result), "explanation_source": "template"}


async def explain(deps: llm.TurnDeps, result: Mapping[str, Any], request: str,
                  context: Sequence[str] = ()) -> dict | None:
    """The result with its explanation, or None when the request is out of scope."""
    facts = fmt.digest(result)
    try:
        parsed = await llm.complete_json(deps, prompts.explain_messages(facts, request, context),
                                         prompts.EXPLAIN_CONTRACT, parse)
    except llm.LlmError:
        return with_template(result)
    if parsed["status"] == "off_topic":
        return None
    checked = check(parsed, facts)
    if checked is None:
        return with_template(result)
    return {**result, "explanation": checked, "explanation_source": "model"}
