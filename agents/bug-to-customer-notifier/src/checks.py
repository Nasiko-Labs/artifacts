"""The code post-checks: the guarantee, not the prompt.

Every id, link, quote and name the model reports must appear in this turn's tool output. Ids are
matched whole; quotes, names and links after normalising case, whitespace and JSON escapes. Dates are
parsed here (ISO 8601 or epoch seconds); an unreadable date is missing, never guessed."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

import tiers
from config import Config
from dates import parse_day
from records import Bug, Ticket

CLOSED_TICKET_STATUSES = frozenset({"solved", "closed"})
MIN_KEYWORD_HITS = 2
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a text says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str, seen: str) -> bool:
    """`needle` occurs in already-normalised tool output. Empty never matches."""
    wanted = normalise_text(needle).strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def id_seen(identifier: str, seen: str) -> bool:
    """A whole id (`PROJ-482`, `4711`) in normalised tool output: not part of a longer id."""
    wanted = normalise_text(identifier)
    if not wanted:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])", seen) is not None


@dataclass(frozen=True)
class BugCheck:
    kept: tuple[Bug, ...] = ()            # verified, fixed, with at least one verified fix version
    no_fix_version: tuple[Bug, ...] = ()  # verified and fixed, but no fix version to check
    not_done: tuple[Bug, ...] = ()        # verified, but the tracker status is not a fixed one
    dropped: int = 0                      # not in tool output, or (sweep) outside the window


def check_bugs(bugs: Iterable[Bug], seen_text: str, config: Config, since: date | None = None) -> BugCheck:
    seen = normalise_text(seen_text)
    result, keys = BugCheck(), frozenset()
    for bug in bugs:
        if bug.key.casefold() in keys or not id_seen(bug.key, seen):
            result = replace(result, dropped=result.dropped + 1)
            continue
        keys = keys | {bug.key.casefold()}
        changed = parse_day(bug.status_changed_at)
        if since is not None and (changed is None or changed < since):
            result = replace(result, dropped=result.dropped + 1)
            continue
        clean = _verified_bug(bug, seen)
        if clean.status.casefold() not in config.done_statuses:
            result = replace(result, not_done=(*result.not_done, clean))
        elif not clean.fix_versions:
            result = replace(result, no_fix_version=(*result.no_fix_version, clean))
        else:
            result = replace(result, kept=(*result.kept, clean))
    return result


def _verified_bug(bug: Bug, seen: str) -> Bug:
    """Only what a tool returned: an unseen link is blanked, an unseen fix version or link dropped."""
    return replace(bug, url=bug.url if appears_in(bug.url, seen) else "",
                   summary=bug.summary if appears_in(bug.summary, seen) else bug.key,
                   description_excerpt=bug.description_excerpt if appears_in(bug.description_excerpt, seen) else "",
                   fix_versions=tuple(v for v in bug.fix_versions if appears_in(v, seen)),
                   remote_links=tuple(link for link in bug.remote_links if appears_in(link, seen)))


@dataclass(frozen=True)
class TicketCheck:
    kept: tuple[Ticket, ...]
    dropped: int      # not in tool output, already solved, or a weak / old keyword match
    capped: int       # over the cap, not kept
    unquoted: int     # kept, but the complaint quote could not be verified


def check_tickets(tickets: Iterable[Ticket], *, seen_text: str, bug: Bug, keywords: Sequence[str],
                  today: date, config: Config) -> TicketCheck:
    seen = normalise_text(seen_text)
    oldest = (parse_day(bug.created_at) or today) - timedelta(days=config.keyword_lookback_days)
    by_id: dict[str, Ticket] = {}
    raw = list(tickets)
    for ticket in raw:
        clean = _verified_ticket(ticket, seen, config)
        if clean is None or not _keep(clean, keywords, oldest):
            continue
        current = by_id.get(clean.id)
        if current is None or (current.match == "keyword" and clean.match == "linked"):
            by_id = {**by_id, clean.id: clean}
    ordered = sorted(by_id.values(), key=lambda t: tiers.priority(t, config))
    kept = tuple(ordered[:config.max_tickets])
    unquoted = sum(1 for t in kept if not t.complaint_quote)
    return TicketCheck(kept, dropped=len(raw) - len(by_id), capped=len(ordered) - len(kept), unquoted=unquoted)


def _verified_ticket(ticket: Ticket, seen: str, config: Config) -> Ticket | None:
    if not id_seen(ticket.id, seen) or ticket.status.casefold() in CLOSED_TICKET_STATUSES:
        return None
    def kept(value: str) -> str:
        return value if appears_in(value, seen) else ""
    hint = kept(ticket.tier_hint)
    return replace(ticket, url=kept(ticket.url), subject=kept(ticket.subject), complaint_quote=kept(ticket.complaint_quote),
                   organization_name=kept(ticket.organization_name), requester_name=kept(ticket.requester_name),
                   tier_hint=hint, tier=tiers.tier_of(hint, config))


def _keep(ticket: Ticket, keywords: Sequence[str], oldest: date) -> bool:
    if ticket.match == "linked":
        return True
    created = parse_day(ticket.created_at)
    if created is None or created < oldest:
        return False
    return keyword_hits(f"{ticket.subject} {ticket.complaint_quote}", keywords) >= MIN_KEYWORD_HITS


def keyword_hits(text: str, keywords: Sequence[str]) -> int:
    lowered = normalise_text(text)
    return sum(1 for word in keywords if word and word.casefold() in lowered)


def kept_keywords(candidates: Sequence[str], bug: Bug, limit: int = 5, min_length: int = 4) -> tuple[str, ...]:
    """Only words that occur in the bug's own summary or description: the model cannot add any."""
    source = normalise_text(f"{bug.summary} {bug.description_excerpt}")
    words = [normalise_text(w) for w in candidates]
    usable = [w for w in words if len(w) >= min_length and w in source]
    return tuple(dict.fromkeys(usable))[:limit]
