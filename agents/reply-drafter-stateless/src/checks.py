"""Pure: the faithfulness checks on a reply (grounding, the shorter alternative, quoted asks), the tone
checks from tone_check.py, and the code fixes applied when the one repair call did not clear them.
Checks never drop a reply."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

import textstats as ts
import tone_check
from draft import Ask, Reply
from grounding import Ground, Span, placeholders, replace_spans, ungrounded
from style import NONE, sign_off_of

ALT_MAX_RATIO = 0.6
ALT_NOT_REQUIRED_WORDS = 25
NO_SHORTER = "No shorter version possible; the draft is already brief."
_PARAGRAPHS = re.compile(r"\n\s*\n")
_TRAILING = re.compile(r"[\s.,!]+$")


@dataclass(frozen=True)
class Basis:
    """What a reply is checked against."""

    ground: Ground                # incoming message + the user's own words
    strict: Ground                # the user's own words only: grounds links, emails and phone numbers
    allowed: Ground               # the measured greeting/sign-off and the sender's name
    target: tone_check.Target
    incoming: str


def spans(reply: Reply, basis: Basis) -> list[Span]:
    return [s for text in (reply.subject, reply.draft, reply.alt_shorter or "")
            for s in ungrounded(text, basis.ground, basis.allowed, basis.strict)]


def problems(reply: Reply, basis: Basis) -> list[str]:
    """Every violation, worded for the repair call."""
    found = [f"'{s.text}' is not in the incoming message or the user's words: write {s.placeholder} instead"
             for s in spans(reply, basis)]
    found += [v.message for v in tone_check.violations(reply.draft, basis.target)]
    found += alt_problems(reply.draft, reply.alt_shorter)
    return list(dict.fromkeys(found))


def alt_limit(draft: str) -> int:
    return math.floor(ALT_MAX_RATIO * ts.word_count(draft))


def alt_required(draft: str) -> bool:
    return ts.word_count(draft) > ALT_NOT_REQUIRED_WORDS


def alt_problems(draft: str, alt: str | None) -> list[str]:
    if not alt_required(draft):
        return []
    limit = alt_limit(draft)
    if not alt:
        return [f"alt_shorter is missing: give the same reply in at most {limit} words"]
    found = []
    if ts.word_count(alt) > limit:
        found.append(f"alt_shorter is {ts.word_count(alt)} words; keep it at most {limit} words (60% of the draft)")
    missing = [p for p in placeholders(draft) if p not in alt]
    if missing:
        found.append(f"alt_shorter must keep the placeholders {', '.join(missing)}")
    closing = sign_off_of(draft)
    if closing != NONE and not _norm(alt).endswith(_norm(closing)):
        found.append(f"alt_shorter must end with the same sign-off: '{closing.replace(chr(10), ' / ')}'")
    return found


def _norm(text: str) -> str:
    return _TRAILING.sub("", re.sub(r"\s+", " ", text.casefold()))


def kept_asks(asks: tuple[Ask, ...], incoming: str) -> tuple[Ask, ...]:
    """Only asks whose quote really is in the incoming message."""
    source = re.sub(r"\s+", " ", incoming.casefold())
    return tuple(a for a in asks if re.sub(r"\s+", " ", a.quote.casefold()).strip(" \"'“”") in source)


@dataclass(frozen=True)
class Fixed:
    reply: Reply
    notes: tuple[str, ...]


def fix(reply: Reply, basis: Basis) -> Fixed:
    """Code fixes for what the repair left: placeholders for made-up specifics, tone and register clean-up,
    and the shorter alternative replaced or omitted."""
    def grounded(text: str) -> str:
        return replace_spans(text, ungrounded(text, basis.ground, basis.allowed, basis.strict))

    draft = tone_check.fix(grounded(reply.draft), basis.target)
    alt = tone_check.fix(grounded(reply.alt_shorter), basis.target) if reply.alt_shorter else None
    alt, notes = fixed_alt(draft, alt)
    return Fixed(replace(reply, subject=grounded(reply.subject), draft=draft, alt_shorter=alt), notes)


def fixed_alt(draft: str, alt: str | None) -> tuple[str | None, tuple[str, ...]]:
    if not alt_required(draft):
        return None, ()
    if alt and not alt_problems(draft, alt):
        return alt, ()
    first = _first_body_paragraph(draft)
    if first and ts.word_count(first) <= alt_limit(draft):
        return first, ()
    return None, (NO_SHORTER,)


def _first_body_paragraph(draft: str) -> str:
    """The first paragraph that is more than a greeting line."""
    for paragraph in _PARAGRAPHS.split(draft.strip()):
        if ts.word_count(paragraph) > 3:
            return paragraph.strip()
    return ""
