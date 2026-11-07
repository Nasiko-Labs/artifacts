"""The one model step: narrate the digest (1 call), check it in code, one repair call when a check fails,
then replace any still-unsupported cause with "driver not provided", else fall back to the code template.
At most 2 calls per turn."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import checks
import llm
import prompts
import template
from digest import Digest
from envelope import Narration

log = logging.getLogger("variance-narrator.narrate")

OFF_TOPIC = "off_topic"


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("ok", OFF_TOPIC):
        return None
    if data["status"] == OFF_TOPIC:
        return {"status": OFF_TOPIC, "narrative": "", "uses": []}
    text = data.get("narrative")
    uses = data.get("uses") or []
    if not isinstance(text, str) or not text.strip() or not isinstance(uses, list):
        return None
    return {"status": "ok", "narrative": " ".join(text.split()), "uses": [str(u) for u in uses]}


def fallback(d: Digest) -> Narration:
    return Narration(template.narrative(d), "template", fallback=True)


async def narrate(deps: llm.TurnDeps, d: Digest, instruction: str, source: str,
                  context: Sequence[str] = ()) -> Narration | None:
    """The checked narration, or None when the model says the request is off topic."""
    messages = prompts.narrate_messages(d.as_text(), instruction, context)
    try:
        parsed = await llm.complete_json(deps, messages, prompts.NARRATE_CONTRACT, parse)
    except llm.LlmError as e:
        log.info("narrate failed (%s); template narrative", e)
        return fallback(d)
    if parsed["status"] == OFF_TOPIC:
        return None
    text, uses = checks.clean_text(parsed["narrative"], source), parsed["uses"]
    problems = checks.check(text, uses, d)
    if not problems.ok:
        text, uses, problems = await _repair(deps, messages, parsed, problems, d, source)
    replaced = 0
    if problems.causes and not (problems.numbers or problems.unknown_ids):
        replaced = len(problems.causes)
        text = checks.replace_causes(text, problems.causes, [ln.item.name for ln in (*d.lines, *d.offsets)])
        problems = checks.check(text, uses, d)
    if not problems.ok or not text.strip():
        log.info("narrative failed its checks (%s); template narrative", problems.describe())
        return fallback(d)
    return Narration(text, "model", replaced=replaced)


async def _repair(deps: llm.TurnDeps, messages: list[dict], parsed: dict, problems: checks.Problems, d: Digest,
                  source: str) -> tuple[str, list[str], checks.Problems]:
    text, uses = checks.clean_text(parsed["narrative"], source), parsed["uses"]
    if not deps.has_time() or not deps.budget.take_repair():
        return text, uses, problems
    answer = json.dumps({"status": "ok", "narrative": parsed["narrative"], "uses": uses})
    try:
        again = parse(llm.load(await llm.call(deps, prompts.repair_narrative_messages(messages, answer,
                                                                                     problems.describe()))))
    except llm.LlmError:
        return text, uses, problems
    if again is None or again["status"] != "ok":
        return text, uses, problems
    text = checks.clean_text(again["narrative"], source)
    return text, again["uses"], checks.check(text, again["uses"], d)
