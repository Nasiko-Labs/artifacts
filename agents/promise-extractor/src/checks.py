"""The code checks: the guarantee, not the prompt.

A candidate survives only when its quote is found verbatim (normalised) in one of the user's messages
(re-pointed when the model named the wrong one) and every number, date word and address in `what` is in
that text. A recipient not written in the message's header, greeting or the quote becomes "Not stated";
a due phrase not in the quote or message is cleared; due dates are resolved here, and a date before the
message was sent is discarded. Duplicates (same normalised quote) are dropped."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import dates
import grounding as g
from messages import Message
from promises import NOT_STATED, Promise, text_of

WHAT_MAX_WORDS = 20
QUOTE_MAX_CHARS = 250
FIELD_MAX_CHARS = 1_000
GENERIC_RECIPIENTS = frozenset({"", "null", "none", "n/a", "na", "unknown", "not stated", "me", "i", "myself",
                                "someone", "somebody", "anyone", "everyone", "you", "them", "they", "we", "us"})
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_HTML = re.compile(r"<[^>]{1,500}>")
_CHANNEL = re.compile(r"#[\w-]+")


@dataclass(frozen=True)
class Checked:
    promises: tuple[Promise, ...]
    dropped: int
    candidates: int


def clean_text(value: Any, source: str = "") -> str:
    """One line of model text: no images, no HTML, no table pipes; a link only if its URL is in the source."""
    text = _IMAGE.sub("", str(value or "")[:FIELD_MAX_CHARS])
    text = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), text)
    return " ".join(_HTML.sub("", text).replace("|", "/").split())


def cut_words(text: str, cap: int) -> str:
    words = text.split()
    return text if len(words) <= cap else " ".join(words[:cap]).rstrip(" ,;:-.") + "…"


def check(candidates: Iterable[Mapping[str, Any]], mine: Sequence[Message], as_of: date, basis_default: str,
          start_order: int = 0) -> Checked:
    """`mine` are the user's messages; `basis_default` is "as_of" or "clock" (how as_of was chosen)."""
    norms = {m.id: g.normalise(m.text) for m in mine}
    lookup = {m.id: m for m in mine}
    kept: list[Promise] = []
    seen: set[str] = set()
    total = dropped = 0
    for raw in candidates:
        total += 1
        promise = _one(raw, lookup, norms, as_of, basis_default, start_order + total)
        key = g.normalise(promise.source_quote) if promise else ""
        if promise is None or key in seen:
            dropped += promise is None
            continue
        seen.add(key)
        kept.append(promise)
    return Checked(tuple(kept), dropped, total)


def _one(raw: Mapping[str, Any], lookup: Mapping[str, Message], norms: Mapping[str, str], as_of: date,
         basis_default: str, order: int) -> Promise | None:
    quote = text_of(raw.get("quote"))
    message = _home(quote, text_of(raw.get("message_id")), lookup, norms)
    if message is None:
        return None
    norm = norms[message.id]
    what = clean_text(raw.get("what"), message.text) or quote
    if g.ungrounded_tokens(what, norm):
        return None
    due_phrase = _due_phrase(text_of(raw.get("due_phrase")), quote, norm)
    due, basis = _due(due_phrase, message, as_of, basis_default)
    return Promise(message_id=message.id, to_whom=grounded_recipient(text_of(raw.get("to")), message, quote),
                   what=cut_words(what, WHAT_MAX_WORDS), source_quote=quote, due_phrase=due_phrase,
                   due=due, sent_on=message.sent_on, basis=basis, order=order)


def _home(quote: str, message_id: str, lookup: Mapping[str, Message],
          norms: Mapping[str, str]) -> Message | None:
    """The message the quote is really in: the one named, else the first that holds it."""
    if len(quote) > QUOTE_MAX_CHARS * 2:
        return None
    named = lookup.get(message_id)
    if named is not None and g.locate(quote, norms[named.id]) is not None:
        return named
    return next((lookup[mid] for mid, norm in norms.items() if g.locate(quote, norm) is not None), None)


def grounded_recipient(to: str, message: Message, quote: str) -> str:
    """The model's recipient if written in the message; none or a pronoun falls back to a written To: line."""
    name = clean_text(to)
    if name.casefold().strip(" .") in GENERIC_RECIPIENTS:
        return clean_text(message.to) or NOT_STATED
    where = g.normalise(" ".join([message.addressing, message.to, quote, *_CHANNEL.findall(message.text)]))
    return name if g.contains(name, where) else NOT_STATED


def _due_phrase(phrase: str, quote: str, norm_message: str) -> str:
    if phrase.casefold() in ("", "null", "none", "n/a", "no date", "not stated"):
        return ""
    return phrase if g.contains(phrase, g.normalise(quote)) or g.contains(phrase, norm_message) else ""


def _due(phrase: str, message: Message, as_of: date, basis_default: str) -> tuple[str | None, str]:
    if not phrase:
        return None, ""
    sent = date.fromisoformat(message.sent_on) if message.sent_on else None
    resolved = dates.resolve(phrase, sent or as_of)
    if resolved is None or (sent is not None and resolved < sent):
        return None, ""
    return resolved.isoformat(), "sent" if sent else basis_default


def merge(stored: Sequence[Promise], new: Sequence[Promise]) -> tuple[tuple[Promise, ...], int]:
    """Stored promises plus the new ones whose normalised quote is not already there; (merged, duplicates)."""
    keys = {g.normalise(p.source_quote) for p in stored}
    fresh = [p for p in new if g.normalise(p.source_quote) not in keys]
    return (*stored, *fresh), len(new) - len(fresh)
