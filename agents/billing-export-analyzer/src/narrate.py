"""The one model step: word the narrative, the cause hypotheses and recommendations from the digest. Code
checks what comes back (checks.py) and falls back to the template narrative when it can't be used."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import llm
import prompts

STATUSES = ("ok", "off_topic")


@dataclass(frozen=True)
class Narrated:
    status: str                  # ok | off_topic
    result: dict | None = None   # the analysis result with narrative, causes and recommendations filled in


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    if data["status"] == "off_topic":
        return {"status": "off_topic"}
    if not isinstance(data.get("narrative"), str):
        return None
    movers = data.get("movers") if isinstance(data.get("movers"), list) else []
    recs = data.get("recommendations") if isinstance(data.get("recommendations"), list) else []
    return {"status": "ok", "narrative": data["narrative"], "movers": movers, "recommendations": recs}


async def narrate(deps: llm.TurnDeps, result: Mapping[str, Any], request: str, context: Sequence[str] = (),
                  source: str = "") -> Narrated:
    digest = result["digest"]
    try:
        parsed = await llm.complete_json(deps, prompts.narrate_messages(digest, request, context),
                                         prompts.NARRATE_CONTRACT, parse)
    except llm.LlmError:
        return Narrated("ok", with_template(result))
    if parsed["status"] == "off_topic":
        return Narrated("off_topic")
    return Narrated("ok", apply(result, checks.check(parsed, digest, source)))


def with_template(result: Mapping[str, Any]) -> dict:
    return {**result, "narrative": checks.template_narrative(result, result["digest"]),
            "narrative_source": "template"}


def apply(result: Mapping[str, Any], checked: checks.Checked) -> dict:
    movers = [{**m, "likely_cause": checked.causes.get(m["name"], {}).get("likely_cause", "")}
              for m in result["top_movers"]]
    waste_recs = [r for r in result["recommendations"] if r["id"].startswith("w")]
    base = {**result, "top_movers": movers, "recommendations": waste_recs + checked.recommendations,
            "dropped": checked.dropped}
    if checked.narrative is None:
        return {**with_template(base), "dropped": checked.dropped + 1}
    return {**base, "narrative": checked.narrative, "narrative_source": "model"}
