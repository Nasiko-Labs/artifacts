"""The content-quality read (one model call, plus one repair): primary topic, intent, answer-first and up
to 5 content issues. Every issue's quote must appear verbatim in the page text, else it is dropped. Issue
labels are fixed text keyed by kind, so model wording never reaches the reply except the topic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import prompts
from fence import strip_markers
from llm import LlmError, TurnDeps, complete_json
from models import PageFacts
from pagetext import verbatim

TEXT_CHARS = 6_000
MAX_ISSUES = 5
TOPIC_MAX_CHARS = 60
QUOTE_MAX_CHARS = 200
CONTRACT = '{"primary_topic": str, "intent": one of informational|commercial|transactional|navigational, ' \
           '"answer_first": bool, "issues": [{"kind": str, "quote": str}]}'


@dataclass(frozen=True)
class QualityRead:
    primary_topic: str = ""
    intent: str = ""
    answer_first: bool | None = None
    issues: list[tuple[str, str]] = field(default_factory=list)   # (label, verbatim quote)
    dropped: int = 0
    failed: bool = False


def head_of(facts: PageFacts) -> dict:
    return {"title": facts.title.text if facts.title else "",
            "meta_description": facts.meta_description.text if facts.meta_description else "",
            "headings": [f"h{level}: {text}" for level, text in facts.outline[:30]]}


async def read(deps: TurnDeps, facts: PageFacts, text: str, context: tuple[str, ...]) -> QualityRead:
    messages = prompts.quality_messages(head_of(facts), strip_markers(text[:TEXT_CHARS]), context)
    try:
        data = await complete_json(deps, messages, CONTRACT, _validate)
    except LlmError:
        return QualityRead(failed=True)
    return _checked(data, text, facts)


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict) or data.get("intent") not in prompts.INTENTS:
        return None
    if not isinstance(data.get("answer_first"), bool) or not isinstance(data.get("issues", []), list):
        return None
    return data


def _checked(data: dict, text: str, facts: PageFacts) -> QualityRead:
    head = " ".join(str(v) for v in head_of(facts).values())
    kept: list[tuple[str, str]] = []
    dropped = 0
    for issue in data.get("issues") or []:
        kind = issue.get("kind") if isinstance(issue, dict) else None
        quote = str(issue.get("quote") or "").strip() if isinstance(issue, dict) else ""
        if kind not in prompts.ISSUE_LABELS or not quote or len(quote) > QUOTE_MAX_CHARS or not verbatim(quote, text,
                                                                                                         head):
            dropped += 1
            continue
        kept.append((prompts.ISSUE_LABELS[kind], quote))
    topic = " ".join(strip_markers(str(data.get("primary_topic") or "")).split())[:TOPIC_MAX_CHARS]
    return QualityRead(primary_topic=topic, intent=data["intent"], answer_first=data["answer_first"],
                       issues=kept[:MAX_ISSUES], dropped=dropped + max(0, len(kept) - MAX_ISSUES))
