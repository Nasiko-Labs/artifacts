"""Pure: turn a step's JSON answer into checked records.

The model only transcribes. A record is kept only when its link and the person it names appear in
THIS step's tool output, its time parses and falls inside the window, and it is not a duplicate or a
bot. Praise must also be quoted verbatim from the tool output, come from someone other than the
person, mention the person with a praise phrase, and come from a channel (never a direct or group
direct message; an unknown conversation type is dropped too).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import people as people_
import praise_terms
from people import Author
from records import LinkedIssue, Person, PraiseItem, PullRequest, Release
from window import Window, parse_time

MAX_PRS_PER_PERSON = 150
MAX_PRAISE_PER_PERSON = 40
MAX_BODY_CHARS = 400
CHANNEL_TYPES = frozenset({"channel", "public_channel", "private_channel", "public", "private"})
DIRECT_TYPES = frozenset({"im", "mpim", "dm", "group_dm", "direct", "direct_message"})
DIRECT_ID_PREFIXES = ("D", "G")
CHANNEL_ID_PREFIX = "C"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class Seen:
    """This step's raw tool output, the only source a record may come from."""

    text: str

    def has(self, value: object) -> bool:
        """The string verbatim in the output, as written or as it reads inside JSON text."""
        if not isinstance(value, str) or not value.strip():
            return False
        needle = value.strip()
        return needle in self.text or json.dumps(needle)[1:-1] in self.text

    def has_number(self, value: object) -> bool:
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        return re.search(rf"(?<![\d.]){value}(?![\d.])", self.text) is not None


@dataclass(frozen=True)
class CodeRecords:
    prs: tuple[PullRequest, ...] = ()
    authors: Mapping[str, tuple[Author, ...]] = field(default_factory=dict)  # person label -> tool identities
    releases: tuple[Release, ...] = ()
    capped: tuple[str, ...] = ()  # labels whose PR list hit the cap


@dataclass(frozen=True)
class ChatRecords:
    praise: tuple[PraiseItem, ...] = ()
    capped: tuple[str, ...] = ()
    direct_dropped: int = 0


def parse_answer(text: str) -> dict | None:
    """The JSON object in a model answer (code fences and surrounding prose tolerated), or None."""
    body = _FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def items(answer: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = answer.get(key)
    return [i for i in raw if isinstance(i, Mapping)] if isinstance(raw, list) else []


def authors_of(answer: Mapping[str, Any], seen: Seen) -> tuple[Author, ...]:
    """Every non-bot PR author the tool output confirms (for inferring people from a repository)."""
    out = [_author(raw, seen) for raw in items(answer, "prs")]
    return tuple(a for a in out if a is not None and a.values() and not people_.is_bot(a))


def check_code(answer: Mapping[str, Any], seen: Seen, window: Window, people: Sequence[Person]) -> CodeRecords:
    kept: dict[str, PullRequest] = {}
    authors: dict[str, tuple[Author, ...]] = {}
    for raw in items(answer, "prs"):
        author = _author(raw, seen)
        if author is None or people_.is_bot(author):
            continue
        person = people_.person_for(author, people)
        pr = _pr(raw, seen, window, person)
        if pr is None or pr.url in kept:
            continue
        kept[pr.url] = pr
        authors[pr.person] = (*authors.get(pr.person, ()), author)
    by_person, capped = _cap(tuple(kept.values()), lambda p: p.person, MAX_PRS_PER_PERSON)
    return CodeRecords(by_person, authors, _releases(answer, seen), capped)


def _author(raw: Mapping[str, Any], seen: Seen) -> Author | None:
    found = Author(handle=_seen_str(raw.get("author"), seen), name=_seen_str(raw.get("author_name"), seen),
                   email=_seen_str(raw.get("author_email"), seen))
    return found if found.values() else None


def _pr(raw: Mapping[str, Any], seen: Seen, window: Window, person: Person | None) -> PullRequest | None:
    url, number = raw.get("url"), _int(raw.get("number"))
    merged = parse_time(raw.get("merged_at"))
    if person is None or not seen.has(url) or number is None or not seen.has_number(number):
        return None
    if merged is None or not window.contains(merged):
        return None  # merged pull requests in the window only
    return PullRequest(
        number=number, url=url.strip(), title=_seen_str(raw.get("title"), seen), person=person.label,
        merged_at=merged, additions=_seen_int(raw.get("additions"), seen),
        deletions=_seen_int(raw.get("deletions"), seen), changed_files=_seen_int(raw.get("changed_files"), seen),
        labels=_seen_list(raw.get("labels"), seen), review_comments=_seen_int(raw.get("review_comments"), seen),
        reviewers=tuple(r for r in _seen_list(raw.get("reviewers"), seen)
                        if people_.person_for(Author(handle=r), (person,)) is None),
        linked=_linked(raw.get("linked_issues"), seen),
    )


def _linked(raw: object, seen: Seen) -> tuple[LinkedIssue, ...]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        ref = item.get("number") if item.get("number") is not None else item.get("url")
        ref_ok = seen.has_number(ref) if isinstance(ref, int) else seen.has(ref)
        if ref_ok:
            out.append(LinkedIssue(str(ref).strip(), _seen_list(item.get("labels"), seen)))
    return tuple(out)


def _releases(answer: Mapping[str, Any], seen: Seen) -> tuple[Release, ...]:
    out = []
    for raw in items(answer, "releases"):
        name, notes = raw.get("name"), raw.get("notes")
        if seen.has(name):
            out.append(Release(name.strip(), notes.strip() if seen.has(notes) else ""))
    return tuple(out)


def check_chat(answer: Mapping[str, Any], seen: Seen, window: Window, people: Sequence[Person]) -> ChatRecords:
    kept: dict[tuple[str, str], PraiseItem] = {}
    direct = 0
    for raw in items(answer, "praise"):
        if _is_direct(raw):
            direct += 1
            continue
        item = _praise(raw, seen, window, people)
        if item is not None:
            kept.setdefault((item.person, item.permalink or item.quote), item)
    ordered = tuple(sorted(kept.values(), key=lambda p: p.ts.timestamp() if p.ts else 0, reverse=True))
    by_person, capped = _cap(ordered, lambda p: p.person, MAX_PRAISE_PER_PERSON)
    return ChatRecords(by_person, capped, direct)


def _is_direct(raw: Mapping[str, Any]) -> bool:
    kind = str(raw.get("channel_type") or "").strip().casefold()
    channel_id = str(raw.get("channel_id") or "").strip()
    if kind in DIRECT_TYPES or channel_id.startswith(DIRECT_ID_PREFIXES):
        return True
    known_channel = kind in CHANNEL_TYPES or (not kind and channel_id.startswith(CHANNEL_ID_PREFIX))
    return not known_channel


def _praise(raw: Mapping[str, Any], seen: Seen, window: Window, people: Sequence[Person]) -> PraiseItem | None:
    person = next((p for p in people if p.label == raw.get("person")), None)
    quote, permalink, author = raw.get("quote"), raw.get("permalink"), raw.get("author")
    if person is None or not seen.has(quote) or not seen.has(permalink) or not seen.has(author):
        return None
    ts = parse_time(raw.get("ts"))
    if ts is None or not window.contains(ts) or not _ts_seen(raw.get("ts"), ts, seen):
        return None
    who = Author(handle=author.strip())
    if people_.is_bot(who) or people_.person_for(who, (person,)) is not None:
        return None  # bots and self-praise
    if not praise_terms.has_praise_phrase(quote) or not _names_person(quote, raw.get("mention"), person, seen):
        return None
    channel = _seen_str(raw.get("channel"), seen) or "a channel"
    return PraiseItem(person.label, quote.strip(), author.strip(), channel.lstrip("#"), permalink.strip(), ts)


def _names_person(quote: str, mention: object, person: Person, seen: Seen) -> bool:
    """The quote names the person by a whole-word alias, or carries the user mention id the tool returned."""
    if people_.mentions(quote, person):
        return True
    return isinstance(mention, str) and len(mention.strip()) > 2 and seen.has(mention) and mention.strip() in quote


def _ts_seen(raw: object, ts, seen: Seen) -> bool:
    return seen.has(str(raw)) or str(int(ts.timestamp())) in seen.text


def _cap(records: tuple, key, limit: int) -> tuple[tuple, tuple[str, ...]]:
    counts: dict[str, int] = {}
    kept, capped = [], []
    for record in records:
        k = key(record)
        counts[k] = counts.get(k, 0) + 1
        if counts[k] <= limit:
            kept.append(record)
        elif k not in capped:
            capped.append(k)
    return tuple(kept), tuple(capped)


def _seen_str(value: object, seen: Seen) -> str:
    return value.strip() if seen.has(value) else ""


def _seen_list(value: object, seen: Seen) -> tuple[str, ...]:
    raw = value if isinstance(value, list) else []
    return tuple(dict.fromkeys(v.strip() for v in raw if isinstance(v, str) and seen.has(v)))


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("#").isdigit():
        return int(value.strip().lstrip("#"))
    return None


def _seen_int(value: object, seen: Seen) -> int | None:
    number = _int(value)
    return number if number is not None and seen.has_number(number) else None
