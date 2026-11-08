"""The write step and the follow-up question, each bounded to the turn's two-call budget.

write:    model → check (numbers, labels, ids) + lint (jargon, causes, length) → done
                    └ any problem → one repair call → checks again → done, or the code template
question: one scoped call (ok | not_in_data | off_topic) → the same number checks and a cause check;
          a failed check answers with the cited facts' own sentences, or "the table doesn't say".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import llm
import prompts
from checks import Checked, check, known_ids
from fact import FactSet
from lint import CAUSAL, lint
from request import Ask
from template import narrate

NOT_JSON = "the answer was not one JSON object with status, headline, narrative and uses"


@dataclass(frozen=True)
class Story:
    status: str                     # ok | off_topic
    headline: str = ""
    body: str = ""
    uses: tuple[str, ...] = ()
    repaired: bool = False
    template: str = ""              # "" | model_failed | checks_failed | requested ("longer")
    dropped: int = 0
    problems: tuple[str, ...] = ()


def parse_written(data: Any) -> dict | None:
    if not isinstance(data, Mapping):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic"}
    headline, body = data.get("headline"), data.get("narrative", "")
    if not isinstance(headline, str) or not headline.strip() or not isinstance(body, str):
        return None
    return {"status": "ok", "headline": headline.strip(), "body": body.strip(), "uses": data.get("uses")}


def _review(data: dict | None, facts: FactSet, ask: Ask) -> tuple[list[str], Checked | None]:
    if data is None:
        return [NOT_JSON], None
    full = f"{data['headline']} {data['body']}"
    checked = check(full, data["uses"], facts, ask.instruction)
    return [*checked.problems, *lint(data["headline"], data["body"], ask)], checked


def _template(facts: FactSet, ask: Ask, why: str, problems: Sequence[str] = ()) -> Story:
    fallback = narrate(facts, ask)
    return Story("ok", fallback.headline, fallback.body, fallback.uses, template=why,
                 dropped=1 if why == "checks_failed" else 0, problems=tuple(problems))


async def write_story(deps: llm.TurnDeps, facts: FactSet, ask: Ask, context: Sequence[str] = ()) -> Story:
    messages = prompts.write_messages(facts, ask, context)
    try:
        raw = await llm.complete(deps, messages)
    except llm.LlmError:
        return _template(facts, ask, "model_failed")
    data = parse_written(llm.load_json(raw))
    if data is not None and data["status"] == "off_topic":
        return Story("off_topic")
    problems, checked = _review(data, facts, ask)
    if not problems:
        return Story("ok", data["headline"], data["body"], checked.uses, dropped=checked.dropped)
    try:
        raw2 = await llm.complete(deps, prompts.repair_messages(messages, raw, problems))
    except llm.LlmError:
        return _template(facts, ask, "model_failed", problems)
    data2 = parse_written(llm.load_json(raw2))
    if data2 is None or data2["status"] != "ok":
        return _template(facts, ask, "checks_failed", problems)
    problems2, checked2 = _review(data2, facts, ask)
    if problems2:
        return _template(facts, ask, "checks_failed", problems2)
    return Story("ok", data2["headline"], data2["body"], checked2.uses, repaired=True, dropped=checked2.dropped)


@dataclass(frozen=True)
class Answer:
    status: str                     # ok | not_in_data | off_topic | failed
    text: str = ""
    uses: tuple[str, ...] = ()


async def answer_question(deps: llm.TurnDeps, facts: FactSet, question: str, instruction: str,
                          context: Sequence[str] = ()) -> Answer:
    try:
        raw = await llm.complete(deps, prompts.question_messages(facts, question, context))
    except llm.LlmError:
        return Answer("failed", prompts.MODEL_FAILED)
    data = llm.load_json(raw)
    status = data.get("status") if isinstance(data, Mapping) else None
    if status == "off_topic":
        return Answer("off_topic")
    text = data.get("answer") if isinstance(data, Mapping) else None
    if status != "ok" or not isinstance(text, str) or not text.strip():
        return Answer("not_in_data", prompts.NOT_IN_DATA)
    checked = check(text, data.get("uses"), facts, instruction)
    if CAUSAL.search(text) and not CAUSAL.search(instruction):
        return Answer("not_in_data", prompts.NOT_IN_DATA)
    if checked.problems:
        cited, _ = known_ids(data.get("uses"), facts.by_id())
        if not cited:
            return Answer("not_in_data", prompts.NOT_IN_DATA)
        by_id = facts.by_id()
        return Answer("ok", " ".join(by_id[i].text for i in cited), cited)
    return Answer("ok", text.strip(), checked.uses)
