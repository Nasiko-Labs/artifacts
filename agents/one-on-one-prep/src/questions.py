"""Questions for the 1:1: model-proposed, checked in code (open-ended start, no yes/no start, <= 18 words, no
judgement word, tied to a real item, nothing ungrounded), then filled to 3 from a fixed standing bank."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

import prompts
from grounding import ungrounded
from judgement import has_judgement
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("one-on-one-prep.questions")

MAX_QUESTIONS = 6
MIN_QUESTIONS = 3
MAX_WORDS = 18
STANDING = (
    "What would make the next two weeks easier?",
    "Where do you want more support from me?",
    "What feedback do you have for me?",
    "What are you learning right now?",
    "Is there anything on your plate you'd like to hand off or drop?",
)
PROUD = "What are you most proud of since our last 1:1?"
_OPEN = re.compile(r"^(?:what|how|where|when|which|tell me|walk me)\b", re.IGNORECASE)
_CLOSED = re.compile(r"^(?:is|are|do|does|did|can|could|will|would|have|has|should|was|were)\b", re.IGNORECASE)


def acceptable(text: str, ground: str) -> bool:
    words = text.split()
    return (bool(words) and len(words) <= MAX_WORDS and bool(_OPEN.match(text)) and not _CLOSED.match(text)
            and text.endswith("?") and not has_judgement(text) and not ungrounded(text, ground))


def checked(data: Any, ids: set[str], ground: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in (data or {}).get("questions") or ():
        if not isinstance(raw, dict):
            continue
        text = " ".join(str(raw.get("text") or "").split())
        about = str(raw.get("about") or "")
        if about not in ids or not acceptable(text, ground) or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append({"id": f"q{len(out) + 1}", "text": text, "source": "model", "about": about})
    return out[:MAX_QUESTIONS]


def fill(questions: list[dict], no_recognition: bool) -> list[dict]:
    """Standing questions until there are at least 3; the 'proud' one first when there is nothing to recognize."""
    bank = ([PROUD] if no_recognition else []) + list(STANDING)
    out = list(questions)
    for text in bank:
        if len(out) >= MIN_QUESTIONS and not (text == PROUD and no_recognition):
            break
        if len(out) >= MAX_QUESTIONS:
            break
        flag = {"flag": "no_recognition"} if text == PROUD else {}
        out.append({"id": f"s{len(out) + 1}", "text": text, "source": "standing", **flag})
    return out


def _validate(data: Any) -> dict | None:
    return data if isinstance(data, dict) and isinstance(data.get("questions"), list) else None


async def propose(deps: TurnDeps, items: Sequence[dict], ground: str, policy: Sequence[str] = ()) -> list[dict]:
    """Checked model questions; [] when there is nothing to ask about or the call failed."""
    if not items or not deps.has_time():
        return []
    block = "\n".join(f"{i['id']}: {i['text']}" for i in items)
    try:
        data = await complete_json(deps, prompts.questions_messages(block, policy), prompts.QUESTIONS_CONTRACT,
                                   _validate)
    except LlmError as e:
        log.warning("questions call failed: %s", e)
        return []
    return checked(data, {i["id"] for i in items}, ground)
