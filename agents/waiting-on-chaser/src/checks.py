"""The code post-check: the guarantee, not the prompt.

An ask survives only when its quote, link and recipient all appear in this turn's tool output, it was
sent inside the window, and it is not addressed to the user. A blocking quote and each later nudge are
kept only when verbatim; a deadline only when on or after the ask. A reply counts only with a verbatim
link, from one of the asked people, after the ask; otherwise the ask is `unclear`."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from asks import Ask, Nudge, ReplyVerdict, Request

MIN_AGE_DAYS = 2
MAX_CHECKED = 25
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SEPARATOR = re.compile(r"[,;]")


@dataclass(frozen=True)
class CheckedSweep:
    asks: tuple[Ask, ...]
    dropped: int = 0      # unbacked, outside the window, or addressed to the user
    too_fresh: int = 0    # younger than MIN_AGE_DAYS: counted, not listed
    capped: int = 0       # over MAX_CHECKED, newest left out


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a sentence says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str | None, seen: str) -> bool:
    """`needle` (a quote, link or id) occurs in normalised tool output. Empty never matches."""
    wanted = normalise_text(needle or "").strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def parse_time(value: str | None) -> datetime | None:
    """An ISO date/datetime or epoch seconds (chat timestamps), as an aware UTC datetime."""
    text = (value or "").strip()
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_day(value: str | None) -> date | None:
    parsed = parse_time(value)
    return parsed.date() if parsed else None


def recipients(value: str) -> frozenset[str]:
    """"Priya <p@x.com>, k@y.com" → {p@x.com, k@y.com}; a chat id stays as it is. Case-folded."""
    found: set[str] = set()
    for raw in _SEPARATOR.split(value or ""):
        part = raw.strip()
        emails = _EMAIL.findall(part)
        if emails:
            found.update(e.casefold() for e in emails)
        elif part:
            found.add(part.casefold())
    return frozenset(found)


def verified_me(me: Iterable[str], seen_text: str) -> tuple[str, ...]:
    """The user's own address/ids, kept only when a tool returned them this turn."""
    seen = normalise_text(seen_text)
    return tuple(m for m in me if appears_in(m, seen))


def post_check(asks: Iterable[Ask], *, now: datetime, request: Request, seen_text: str,
               me: Sequence[str] = ()) -> CheckedSweep:
    seen = normalise_text(seen_text)
    since = now.date() - timedelta(days=request.window_days)
    mine = recipients(", ".join(me))
    backed, dropped, fresh = [], 0, 0
    for ask in asks:
        asked = parse_time(ask.asked_at)
        if asked is None or not _is_backed(ask, asked, seen, since, now) or (mine and recipients(ask.to) <= mine):
            dropped += 1
        elif ask.source in request.sources and _is_for(ask, request.person):
            if not request.include_fresh and now - asked < timedelta(days=MIN_AGE_DAYS):
                fresh += 1
            else:
                backed.append(_cleaned(ask, asked, seen, now))
    unique = _dedupe(backed)
    shown = unique[:MAX_CHECKED]
    keyed = tuple(replace(a, key=f"a{n}", id=f"a{n}") for n, a in enumerate(shown, start=1))
    return CheckedSweep(keyed, dropped, fresh, len(unique) - len(shown))


def _is_backed(ask: Ask, asked: datetime, seen: str, since: date, now: datetime) -> bool:
    in_window = since <= asked.date() and asked <= now
    on_record = all(appears_in(part, seen) for part in recipients(ask.to)) and bool(recipients(ask.to))
    return in_window and on_record and appears_in(ask.quote, seen) and appears_in(ask.link, seen)


def _is_for(ask: Ask, person: str | None) -> bool:
    if not person:
        return True
    wanted = person.casefold()
    return wanted in ask.to.casefold() or wanted in ask.to_name.casefold()


def _cleaned(ask: Ask, asked: datetime, seen: str, now: datetime) -> Ask:
    deadline = parse_day(ask.deadline_date)
    nudges = tuple(n for n in ask.later_nudges if _is_later_nudge(n, asked, seen, now))
    return replace(ask, deadline_date=deadline.isoformat() if deadline and deadline >= asked.date() else None,
                   blocking_quote=ask.blocking_quote if appears_in(ask.blocking_quote, seen) else None,
                   later_nudges=nudges, reply_state="waiting", reply_checked=False, reply_link=None)


def _is_later_nudge(nudge: Nudge, asked: datetime, seen: str, now: datetime) -> bool:
    at = parse_time(nudge.at)
    return at is not None and asked < at <= now and appears_in(nudge.link, seen)


def _dedupe(asks: Sequence[Ask]) -> list[Ask]:
    """One ask per (thread, person): the earliest. Result oldest first."""
    ordered = sorted(asks, key=lambda a: parse_time(a.asked_at) or datetime.max.replace(tzinfo=UTC))
    kept: dict[tuple[str, frozenset[str]], Ask] = {}
    for ask in ordered:
        kept.setdefault((normalise_text(ask.thread_ref or ask.link), recipients(ask.to)), ask)
    return list(kept.values())


def apply_replies(asks: Sequence[Ask], verdicts: Mapping[str, ReplyVerdict], seen_text: str) -> tuple[Ask, ...]:
    """`replied` only with a verbatim link, from an asked person, after the ask, else `unclear`;
    no verdict → still waiting, flagged as not checked."""
    seen = normalise_text(seen_text)
    return tuple(_judged(a, verdicts.get(a.key), seen) for a in asks)


def _judged(ask: Ask, verdict: ReplyVerdict | None, seen: str) -> Ask:
    if verdict is None:
        return replace(ask, reply_state="waiting", reply_checked=False, reply_link=None)
    if verdict.state != "replied":
        return replace(ask, reply_state=verdict.state, reply_checked=True, reply_link=None)
    state = "replied" if _reply_is_backed(ask, verdict, seen) else "unclear"
    return replace(ask, reply_state=state, reply_checked=True, reply_link=verdict.reply_link)


def _reply_is_backed(ask: Ask, verdict: ReplyVerdict, seen: str) -> bool:
    asked, answered = parse_time(ask.asked_at), parse_time(verdict.reply_at)
    in_order = asked is not None and answered is not None and answered > asked
    return in_order and appears_in(verdict.reply_link, seen) and _from_asked(ask, verdict.reply_from)


def _from_asked(ask: Ask, reply_from: str | None) -> bool:
    who = recipients(reply_from or "")
    if who and who <= recipients(ask.to):
        return True
    name = (reply_from or "").strip().casefold()
    return bool(name) and name == ask.to_name.strip().casefold()
