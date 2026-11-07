"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the checks, never taken from the model."""

from __future__ import annotations

import json
from collections.abc import Sequence

import gaps
from prd import Fact, Prd

HIGH_MIN_FACTS = 8
HIGH_MAX_PROPOSED = 0.2
LOW_MIN_PROPOSED = 0.5
LOW_MAX_BRIEF_WORDS = 80


def confidence(prd: Prd, facts: Sequence[Fact], brief_words: int) -> str:
    share = prd.proposed_share()
    if not prd.problem or share > LOW_MIN_PROPOSED or brief_words < LOW_MAX_BRIEF_WORDS:
        return "low"
    if len(facts) >= HIGH_MIN_FACTS and not gaps.blocking(prd) and share <= HIGH_MAX_PROPOSED:
        return "high"
    return "medium"


def build(prd: Prd, facts: Sequence[Fact], brief_words: int, markdown: str, dropped: int) -> dict:
    level = confidence(prd, facts, brief_words)
    share = round(prd.proposed_share(), 2)
    reqs, questions = len(prd.requirements), len(prd.open_questions)
    summary = f"PRD for {prd.title}: {reqs} requirements, {questions} open questions, {round(share * 100)}% proposed"
    return {"summary": summary,
            "items": [{"question": q.question, "section": q.section, "blocking": q.blocking}
                      for q in prd.open_questions],
            "prd_markdown": markdown, "confidence": level,
            "needs_human": level == "low" or gaps.blocking(prd),
            "sections": prd.to_dict(), "proposed_share": share, "dropped": dropped}


def as_json_text(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
