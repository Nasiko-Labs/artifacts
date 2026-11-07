"""Pure: the workflow envelope (field names as in the catalog) and its fenced-json text form.

A workflow step reads only text, so with "as json" in the request the text part IS the envelope in a
fenced json block; the same envelope always travels as a data part too."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from model import Untangled
from owners import is_mine

_AS_JSON = re.compile(r"\b(?:as|in) json\b|\bjson only\b|\bjson output\b|^\s*json\s*$", re.IGNORECASE)


def wants_json(text: str) -> bool:
    return bool(_AS_JSON.search(text or ""))


def build(result: Untangled, my_name: str = "", speakers: Sequence[str] = ()) -> dict:
    actions = [{"owner": a.owner, "task": a.task, "due": a.due or None,
                "mine": is_mine(a.owner, my_name, speakers), "quote": a.quote, "message_id": a.message_id,
                "status": a.status} for a in result.actions]
    return {
        "summary": result.tldr,
        "tldr": result.tldr,
        "items": actions,
        "decisions": [{"text": d.text, "quote": d.quote, "message_id": d.message_id, "speaker": d.speaker,
                       "status": d.status} for d in result.decisions],
        "open_questions": [{"text": q.text, "asked_by": q.asked_by, "quote": q.quote, "message_id": q.message_id}
                           for q in result.questions],
        "action_items": actions,
        "reversals": [{"decision": r.decision_text, "overridden_in": r.later_message_id, "speaker": r.speaker,
                       "what_changed": r.what_changed, "quote": r.quote} for r in result.reversals],
        "confidence": result.confidence,
        "needs_human": result.needs_human,
    }


def as_text(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"
