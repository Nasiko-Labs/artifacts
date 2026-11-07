"""The one model step: a short summary and recommendations worded from the digest. Code checks what comes back
(checks.py) and falls back to each rule's template when the call fails or an item can't be used."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import llm
import prompts

STATUSES = ("ok", "off_topic")


@dataclass(frozen=True)
class Recommended:
    status: str                  # ok | off_topic
    result: dict | None = None


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    if data["status"] == "off_topic":
        return {"status": "off_topic"}
    if not isinstance(data.get("summary"), str):
        return None
    recs = data.get("recommendations") if isinstance(data.get("recommendations"), list) else []
    return {"status": "ok", "summary": data["summary"], "recommendations": recs}


async def recommend(deps: llm.TurnDeps, result: Mapping[str, Any], request: str, context: Sequence[str] = (),
                    source: str = "") -> Recommended:
    try:
        parsed = await llm.complete_json(deps, prompts.recommend_messages(result["digest"], request, context),
                                         prompts.RECOMMEND_CONTRACT, parse)
    except llm.LlmError:
        return Recommended("ok", with_template(result))
    if parsed["status"] == "off_topic":
        return Recommended("off_topic")
    return Recommended("ok", apply(result, checks.check(parsed, result["digest"], source)))


def with_template(result: Mapping[str, Any], dropped: int = 0) -> dict:
    return {**result, "summary": checks.template_summary(result["digest"], result["team_label"]),
            "recommendations": checks.template_recommendations(result["waste"]), "rec_source": "template",
            "recs_dropped": dropped}


def apply(result: Mapping[str, Any], checked: checks.Checked) -> dict:
    covered = {r["finding_id"] for r in checked.recommendations}
    fill = [r for r in checks.template_recommendations(result["waste"]) if r["finding_id"] not in covered]
    order = {f["id"]: i for i, f in enumerate(result["waste"])}
    recs = sorted(checked.recommendations + fill, key=lambda r: order.get(r["finding_id"], 0))
    summary = checked.summary
    return {**result, "recommendations": recs, "recs_dropped": checked.dropped,
            "summary": summary or checks.template_summary(result["digest"], result["team_label"]),
            "rec_source": "model" if summary else "template"}
