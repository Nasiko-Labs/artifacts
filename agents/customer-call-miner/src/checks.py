"""Pure post-checks: what the model said is kept only when a tool output backs it.

- Calls: id must appear in tool output, start inside the window, at least one external participant.
- Items: the quote must be verbatim in THAT call's transcript (normalised), inside a customer turn;
  speaker and offset come from that turn, never from the model; a competitor must be named in the quote.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from config import Config
from records import ITEM_TYPES, Call, Item, Participant, Turn, text_of
from windows import Window, parse_instant

_CURLY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "–": "-"})
_SPACES = re.compile(r"\s+")
_EDGE = " \"'.,;:!?…-"
_QUOTE_MARKS = " \"'\u201c\u201d\u2018\u2019"
_LABEL_WORDS = 6

DROP_NOT_FOUND = "quote not found in the transcript"
DROP_NOT_CUSTOMER = "not said by a customer"
DROP_COMPETITOR = "competitor not named in the quote"
DROP_MALFORMED = "incomplete item"


def normalise(text: str) -> str:
    return _SPACES.sub(" ", (text or "").translate(_CURLY)).strip().casefold()


@dataclass(frozen=True)
class CallCheck:
    calls: tuple[Call, ...]
    dropped: int          # not backed by tool output, or outside the window
    internal_only: int    # no external participant


def checked_calls(raw: Sequence[object], seen_text: str, window: Window, config: Config) -> CallCheck:
    entries = [e for e in raw if isinstance(e, Mapping)]
    own = config.own_domains | _internal_domains(entries, config)
    kept: dict[str, Call] = {}
    dropped = internal_only = 0
    for entry in entries:
        call = _call(entry, seen_text, own)
        instant = parse_instant(text_of(entry.get("started_at")))
        if call is None or instant is None or not window.contains(instant):
            dropped += 1
        elif not any(p.external for p in call.participants):
            internal_only += 1
        else:
            kept.setdefault(call.id, call)
    return CallCheck(tuple(kept.values()), dropped, internal_only)


def _internal_domains(entries: Sequence[Mapping], config: Config) -> frozenset[str]:
    """Domains of participants the call-recording tool itself marks as internal."""
    return frozenset(_domain(text_of(p.get("email"))) for e in entries for p in _people(e)
                     if text_of(p.get("affiliation")).casefold() == "internal" and "@" in text_of(p.get("email"))
                     and _domain(text_of(p.get("email"))) not in config.free_mail)


def _people(entry: Mapping) -> list[Mapping]:
    people = entry.get("participants")
    return [p for p in people if isinstance(p, Mapping)] if isinstance(people, list) else []


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().casefold().removeprefix("www.")


def _call(entry: Mapping, seen_text: str, own: frozenset[str]) -> Call | None:
    call_id, url = text_of(entry.get("id")), text_of(entry.get("url"))
    if not call_id or call_id.casefold() not in seen_text.casefold():
        return None
    duration = entry.get("duration_s")
    people = tuple(_participant(p, seen_text, own) for p in _people(entry))
    return Call(id=call_id, url=url if url and url in seen_text else "", title=text_of(entry.get("title")),
                started_at=text_of(entry.get("started_at")),
                duration_s=float(duration) if isinstance(duration, (int, float)) else None, participants=people)


def _participant(raw: Mapping, seen_text: str, own: frozenset[str]) -> Participant:
    email = text_of(raw.get("email"))
    email = email if "@" in email and email.casefold() in seen_text.casefold() else ""
    affiliation = text_of(raw.get("affiliation")).casefold()
    internal = affiliation == "internal" or (bool(email) and _domain(email) in own)
    external = not internal and (affiliation == "external" or bool(email))
    return Participant(name=text_of(raw.get("name")), email=email, external=external,
                       speaker_id=text_of(raw.get("speaker_id")))


@dataclass(frozen=True)
class ItemCheck:
    items: tuple[Item, ...]
    dropped: Counter


def checked_items(raw: Sequence[object], call: Call, turns: Sequence[Turn], account_id: str | None,
                  config: Config) -> ItemCheck:
    kept: list[Item] = []
    dropped: Counter = Counter()
    for entry in raw[:config.max_items_per_chunk]:
        item, reason = _item(entry, call, turns, account_id, config)
        if item is None:
            dropped[reason] += 1
        elif item not in kept:
            kept.append(item)
    return ItemCheck(tuple(kept), dropped)


def _item(entry: object, call: Call, turns: Sequence[Turn], account_id: str | None,
          config: Config) -> tuple[Item | None, str]:
    if not isinstance(entry, Mapping):
        return None, DROP_MALFORMED
    kind, quote = text_of(entry.get("type")), text_of(entry.get("quote")).strip(_QUOTE_MARKS)
    if kind not in ITEM_TYPES or not quote or len(quote) > config.max_quote_chars:
        return None, DROP_MALFORMED
    turn = locate(quote, turns)
    if turn is None:
        return None, DROP_NOT_FOUND
    if not turn.external:
        return None, DROP_NOT_CUSTOMER
    competitor = competitor_name(text_of(entry.get("competitor")), quote, config.competitor_aliases)
    if kind == "competitor" and competitor is None:
        return None, DROP_COMPETITOR
    label = " ".join(text_of(entry.get("label")).split()[:_LABEL_WORDS]) or kind.replace("_", " ")
    return Item(call_id=call.id, account_id=account_id, type=kind, quote=quote, speaker=turn.speaker,
                offset_s=turn.offset_s, label=label, competitor=competitor), ""


def locate(quote: str, turns: Sequence[Turn]) -> Turn | None:
    """The turn holding the quote, a customer turn first (a rep may repeat the customer's words)."""
    wanted = normalise(quote).strip(_EDGE)
    if not wanted:
        return None
    hits = [t for t in turns if wanted in normalise(t.text)]
    return next((t for t in hits if t.external), hits[0] if hits else None)


def competitor_name(raw: str, quote: str, aliases: Mapping[str, str]) -> str | None:
    """The competitor as named in the quote, mapped through the alias list; None when the quote lacks it."""
    name = raw.strip()
    if not name or normalise(name) not in normalise(quote):
        return None
    return aliases.get(name.casefold(), name)
