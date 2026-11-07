"""The context step: one LLM call per batch of described events writes a one-line context from each
event's own description; code keeps a line only when its evidence phrase occurs in that description and
every number or link in it does too. An event with no description gets no LLM call."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

import prompts
from dayplan import key_of
from deps import TurnDeps
from llm import ask_once
from models import Event
from verbatim import Seen, load_json_object

log = logging.getLogger("day_planner.context")

BATCH_SIZE = 8
MAX_CONTEXT_CHARS = 140
MAX_DESCRIPTION_CHARS = 2_000
_CHECKED = re.compile(r"https?://\S+|\d[\d:.,/%-]*")


class _Item(BaseModel):
    id: str
    context: str | None = None
    evidence: str | None = None


class _Answer(BaseModel):
    items: list[_Item]


@dataclass(frozen=True)
class ContextResult:
    lines: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)  # event key -> (context, evidence)
    dropped: int = 0
    llm_calls: int = 0


async def contexts(deps: TurnDeps, events: Sequence[Event], max_calls: int) -> ContextResult:
    described = [e for e in events if e.raw.description.strip()]
    if not described or max_calls < 1:
        return ContextResult()
    size = max(BATCH_SIZE, math.ceil(len(described) / max_calls))
    lines: dict[str, tuple[str | None, str | None]] = {}
    dropped = calls = 0
    for start in range(0, len(described), size):
        batch = described[start:start + size]
        answer, used = await _ask(deps, batch, max_calls - calls)
        calls += used
        kept, lost = _verified(batch, answer)
        lines = {**lines, **kept}
        dropped += lost
    return ContextResult(lines, dropped, calls)


async def _ask(deps: TurnDeps, batch: Sequence[Event], allowed: int) -> tuple[_Answer | None, int]:
    items = [{"id": f"e{i + 1}", "title": prompts.strip_markers(e.raw.title),
              "description": prompts.strip_markers(e.raw.description)[:MAX_DESCRIPTION_CHARS]}
             for i, e in enumerate(batch)]
    messages = [{"role": "system", "content": prompts.CONTEXT_SYSTEM},
                {"role": "user", "content": prompts.fence_events(items)}]
    text = await ask_once(deps, messages)
    answer = _parse(text)
    if answer is not None or allowed < 2:
        return answer, 1
    log.info("context answer was not the JSON; one repair call")
    repaired = await ask_once(deps, [*messages, {"role": "assistant", "content": text},
                                     {"role": "user", "content": prompts.CONTEXT_REPAIR}])
    return _parse(repaired), 2


def _parse(text: str) -> _Answer | None:
    data = load_json_object(text)
    if data is None:
        return None
    try:
        return _Answer.model_validate(data)
    except ValidationError:
        return None


def _verified(batch: Sequence[Event], answer: _Answer | None) -> tuple[dict, int]:
    """Lines whose evidence and numbers/links occur in the event's own description; others become None."""
    by_id = {f"e{i + 1}": e for i, e in enumerate(batch)}
    kept: dict[str, tuple[str | None, str | None]] = {}
    dropped = 0
    for item in (answer.items if answer else ()):
        event = by_id.get(item.id)
        if event is None or not (item.context or "").strip():
            continue
        if _backed(item, event.raw.description):
            kept[key_of(event)] = (_trim(item.context.strip()), item.evidence.strip())
        else:
            dropped += 1
            log.info("context for one event dropped: evidence not in its description")
    return kept, dropped + (len(batch) if answer is None else 0)


def _backed(item: _Item, description: str) -> bool:
    seen = Seen(description)
    if not item.evidence or not seen.has(item.evidence):
        return False
    return all(seen.has(token) for token in _CHECKED.findall(item.context or ""))


def _trim(text: str) -> str:
    return text if len(text) <= MAX_CONTEXT_CHARS else text[:MAX_CONTEXT_CHARS - 1].rstrip() + "…"
