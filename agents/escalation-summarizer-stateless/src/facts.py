"""Facts: what the model extracted, kept only when code finds its quote in the thread.

A fact's snippet must be in the thread (normalised, at least 12 chars) and every number, date word,
email and URL in its text must be there too. A wrong `message` is corrected to the message that holds
the snippet. Speaker and timestamp come from that message, never from the model."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import grounding as g
from messages import Message, index_of, locate_message

KINDS = ("problem", "customer", "impact", "attempted", "status", "ask", "deadline", "blocker", "stakeholder")
EXTRACT_STATUSES = ("ok", "off_topic", "not_thread")
TEXT_MAX_WORDS = 25
SNIPPET_MAX_CHARS = 200
_SPACES = re.compile(r"\s+")
_MARKUP = re.compile(r"<[^>]{1,300}>|!\[[^\]]*\]\([^)]*\)")


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    text: str
    snippet: str
    message: str = ""
    speaker: str = ""
    timestamp: str = ""
    contradicts: str = ""   # id of a fact this one disagrees with (the model flags it; code renders it)


@dataclass(frozen=True)
class Verified:
    facts: tuple[Fact, ...]
    dropped: int


def parse_extract(data: Any) -> dict | None:
    """Shape check of the extraction JSON; None triggers one repair call."""
    if not isinstance(data, Mapping) or data.get("status") not in EXTRACT_STATUSES:
        return None
    facts = data.get("facts") or []
    if not isinstance(facts, list) or not all(isinstance(f, Mapping) for f in facts):
        return None
    return {"status": data["status"], "facts": [dict(f) for f in facts]}


def clean(text: Any) -> str:
    return _SPACES.sub(" ", _MARKUP.sub("", str(text or "")).replace("|", "/")).strip()


def cut_words(text: str, cap: int) -> str:
    words = text.split()
    return text if len(words) <= cap else " ".join(words[:cap]).rstrip(" ,;:-.") + "…"


def verify(entries: Iterable[Mapping[str, Any]], thread: str, messages: tuple[Message, ...]) -> Verified:
    """Checked facts in input order (ids still the model's; `renumber` assigns final ones)."""
    norm = g.normalise(thread)
    kept, dropped = [], 0
    for entry in entries:
        fact = _verify_one(entry, norm, messages)
        if fact is None:
            dropped += 1
        else:
            kept.append(fact)
    return Verified(tuple(kept), dropped)


def _verify_one(entry: Mapping[str, Any], norm_thread: str, messages: tuple[Message, ...]) -> Fact | None:
    kind = str(entry.get("kind") or "").strip().casefold()
    text = clean(entry.get("text"))
    snippet = _SPACES.sub(" ", str(entry.get("snippet") or "")).strip()
    if kind not in KINDS or not text or g.locate(snippet, norm_thread) is None:
        return None
    if g.ungrounded_tokens(text, norm_thread):
        return None
    hint = str(entry.get("message") or "").strip() or None
    message = locate_message(snippet, messages, hint)
    capped = snippet if len(snippet) <= SNIPPET_MAX_CHARS else snippet[:SNIPPET_MAX_CHARS].rstrip() + "…"
    return Fact(id=str(entry.get("id") or ""), kind=kind, text=cut_words(text, TEXT_MAX_WORDS), snippet=capped,
                message=message.id if message else "", speaker=message.speaker if message else "",
                timestamp=message.timestamp if message else "", contradicts=str(entry.get("contradicts") or ""))


def merge(groups: Sequence[Sequence[Fact]]) -> tuple[Fact, ...]:
    """Union of facts from all parts, deduplicated by normalised text, in thread order, with final ids
    f1..fn (a `contradicts` link is remapped, or cleared when its target did not survive)."""
    seen: set[str] = set()
    ordered: list[Fact] = []
    for group in groups:
        for fact in group:
            key = g.normalise(fact.text).strip(" .…")
            if key not in seen:
                seen.add(key)
                ordered.append(fact)
    ordered.sort(key=lambda f: index_of(f.message))
    return renumber(ordered)


def renumber(facts: Sequence[Fact]) -> tuple[Fact, ...]:
    new_ids: dict[str, str] = {}
    for n, fact in enumerate(facts, start=1):
        if fact.id and fact.id not in new_ids:
            new_ids[fact.id] = f"f{n}"
    out = []
    for n, fact in enumerate(facts, start=1):
        target = new_ids.get(fact.contradicts, "") if fact.contradicts else ""
        out.append(Fact(f"f{n}", fact.kind, fact.text, fact.snippet, fact.message, fact.speaker, fact.timestamp,
                        target if target != f"f{n}" else ""))
    return tuple(out)


def prefixed(facts: Sequence[Fact], prefix: str) -> tuple[Fact, ...]:
    """Chunk-scoped model ids, so ids from different parts never collide before `merge`."""
    return tuple(Fact(f"{prefix}{f.id}", f.kind, f.text, f.snippet, f.message, f.speaker, f.timestamp,
                      f"{prefix}{f.contradicts}" if f.contradicts else "") for f in facts)


def of_kind(facts: Sequence[Fact], *kinds: str) -> tuple[Fact, ...]:
    return tuple(f for f in facts if f.kind in kinds)


def latest(facts: Sequence[Fact], kind: str) -> Fact | None:
    """The fact of `kind` from the latest message (later messages win)."""
    found = of_kind(facts, kind)
    return max(found, key=lambda f: index_of(f.message)) if found else None


def block(facts: Sequence[Fact]) -> str:
    """The FACTS block given to the model: id, kind, where, text and quote."""
    lines = []
    for f in facts:
        where = ", ".join(p for p in (f.message, f.speaker, f.timestamp) if p)
        lines.append(f'{f.id} [{f.kind}]{f" ({where})" if where else ""} {f.text} | quote: "{f.snippet}"')
    return "\n".join(lines)


def to_state(facts: Sequence[Fact]) -> list[dict]:
    return [asdict(f) for f in facts]


def from_state(data: Any) -> tuple[Fact, ...]:
    return tuple(Fact(**{k: str(v or "") for k, v in dict(d).items() if k in Fact.__dataclass_fields__})
                 for d in data or ())
