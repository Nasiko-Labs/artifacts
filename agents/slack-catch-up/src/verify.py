"""Pure: keep only what the channel's own messages prove. A decision needs a verbatim quote from one
human message and a named actor who wrote in that channel; its ts and link come from that message, not
from the model. A reversal counts only when its quote verifies in a LATER message. A highlight needs a
verbatim quote, and any number in its line must appear in the channel's text. At most 3 highlights."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from models import Message
from render import when

MAX_HIGHLIGHTS = 3
MAX_DECISIONS = 10
_SPACES = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True)
class Verified:
    decisions: tuple[dict, ...]
    highlights: tuple[dict, ...]
    dropped: int


def _norm(text: str) -> str:
    return _SPACES.sub(" ", text).strip().casefold()


def find(quote: Any, messages: Sequence[Message]) -> Message | None:
    """The first human message containing the quote verbatim (whitespace and case relaxed)."""
    if not isinstance(quote, str) or len(_norm(quote)) < 3:
        return None
    needle = _norm(quote)
    return next((m for m in messages if m.is_human and needle in _norm(m.text)), None)


def check(answer: Mapping[str, Any], messages: Sequence[Message]) -> Verified:
    decisions, d_dropped = _decisions(_items(answer, "decisions"), messages)
    highlights, h_dropped = _highlights(_items(answer, "highlights"), messages)
    return Verified(decisions, highlights, d_dropped + h_dropped)


def _items(answer: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = answer.get(key)
    return [i for i in raw if isinstance(i, Mapping)] if isinstance(raw, list) else []


def _decisions(raw: list[Mapping[str, Any]], messages: Sequence[Message]) -> tuple[tuple[dict, ...], int]:
    authors = {m.author.casefold() for m in messages if m.is_human}
    kept: dict[str, dict] = {}
    dropped = 0
    for item in raw:
        source = find(item.get("quote"), messages)
        actor = str(item.get("decided_by") or "").strip().lstrip("@")
        line = str(item.get("decision") or "").strip()
        if source is None or not actor or actor.casefold() not in authors or not line or not _numbers_ok(line, messages):
            dropped += 1
            continue
        later = find(item.get("superseded_by_quote"), [m for m in messages if (m.time or 0) > (source.time or 0)])
        kept.setdefault(source.ts, {"decision": line, "quote": item["quote"].strip(), "decided_by": actor,
                                    "ts": source.ts, "time": source.time_label or when(source.ts),
                                    "link": source.permalink, "later_changed": later is not None,
                                    "changed_quote": later.text.strip() if later else ""})
    out = tuple(sorted(kept.values(), key=lambda d: float(d["ts"])))
    return out[:MAX_DECISIONS], dropped + max(0, len(out) - MAX_DECISIONS)


def _highlights(raw: list[Mapping[str, Any]], messages: Sequence[Message]) -> tuple[tuple[dict, ...], int]:
    kept: dict[str, dict] = {}
    dropped = 0
    for item in raw:
        source = find(item.get("quote"), messages)
        line = str(item.get("line") or "").strip()
        if source is None or not line or not _numbers_ok(line, messages):
            dropped += 1
            continue
        kept.setdefault(source.ts, {"line": line, "quote": item["quote"].strip(), "author": source.author,
                                    "ts": source.ts, "time": source.time_label or when(source.ts),
                                    "link": source.permalink})
    out = tuple(sorted(kept.values(), key=lambda h: float(h["ts"])))[:MAX_HIGHLIGHTS]
    return out, dropped


def _numbers_ok(line: str, messages: Sequence[Message]) -> bool:
    text = "\n".join(m.text for m in messages)
    return all(n in text for n in _NUMBER.findall(line))
