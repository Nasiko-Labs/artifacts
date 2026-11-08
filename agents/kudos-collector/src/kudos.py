"""Pure: which merged pull requests are notable (and why), which shipped, and each person's packet.

Every rule is code with a rule id, so a manager can audit why a pull request is listed. No score, no
ordering of people (alphabetical), no "most" or "top" anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import praise_terms
from records import (
    NotablePR,
    Person,
    PersonPacket,
    PraiseItem,
    PullRequest,
    Release,
    ShippedItem,
)

NOTABLE_LINES = 200
NOTABLE_FILES = 3
REVIEW_COMMENTS = 8
REVIEWERS = 2
TRIVIAL_LINES = 10
LINKED_LABELS = frozenset({"bug", "incident", "security", "customer", "performance"})
NOTABLE_LABELS = frozenset({"feature", "enhancement", "breaking", "security", "performance", "reliability"})
SHIPPED_LABELS = frozenset({"feature", "enhancement"})
TRIVIAL_PREFIXES = ("chore", "bump", "deps", "typo", "docs: fix", "revert")
RULES = ("size", "review", "linked", "label")
NO_PRAISE_GAP = ("No praise found in channels I can read; direct messages and channels you aren't in are "
                 "invisible to me.")
NO_PRS_GAP = "No merged pull requests found for them in the window."
NO_RECORDS_GAP = "No records found at all: check the name or handle."


def is_trivial(pr: PullRequest) -> bool:
    title = pr.title.strip().casefold()
    if title.startswith(TRIVIAL_PREFIXES):
        return True
    lines = _lines(pr)
    return lines is not None and lines < TRIVIAL_LINES


def notable(pr: PullRequest) -> NotablePR | None:
    if pr.pasted:
        return NotablePR(pr, ("pasted",), ("pasted by you",))
    if is_trivial(pr):
        return None
    hits = [(rule, reason) for rule, reason in (_size(pr), _review(pr), _linked(pr), _label(pr)) if rule]
    if not hits:
        return None
    return NotablePR(pr, tuple(r for r, _ in hits), tuple(text for _, text in hits))


def shipped(item: NotablePR, releases: Sequence[Release]) -> ShippedItem | None:
    pr = item.pr
    if pr.pasted:
        return None
    release = next((r.name for r in releases if _in_release(pr, r)), "")
    labelled = bool(SHIPPED_LABELS & _lower(pr.labels))
    if labelled or pr.title.strip().casefold().startswith("feat") or release:
        return ShippedItem(pr, release)
    return None


def packet(person: Person, prs: Sequence[PullRequest], praise: Sequence[PraiseItem], releases: Sequence[Release],
           read_code: bool, read_chat: bool) -> PersonPacket:
    mine = sorted((p for p in prs if p.person == person.label), key=_merged_key, reverse=True)
    notables = tuple(n for n in (notable(p) for p in mine) if n is not None)
    ships = tuple(s for s in (shipped(n, releases) for n in notables) if s is not None)
    quotes = tuple(sorted((q for q in praise if q.person == person.label),
                          key=lambda q: q.ts.timestamp() if q.ts else 0, reverse=True))
    shown = tuple(PraiseItem(q.person, praise_terms.truncate_quote(q.quote), q.author, q.channel, q.permalink, q.ts,
                             q.pasted) for q in quotes)
    merged = sum(1 for p in mine if not p.pasted)
    praisers = len({q.author.casefold() for q in quotes if q.author})
    return PersonPacket(person, merged, notables, ships, shown, praisers,
                        _gaps(mine, quotes, read_code, read_chat))


def _gaps(prs, praise, read_code: bool, read_chat: bool) -> tuple[str, ...]:
    if not prs and not praise:
        return (NO_RECORDS_GAP,)
    gaps = []
    if read_code and not prs:
        gaps.append(NO_PRS_GAP)
    if read_chat and not praise:
        gaps.append(NO_PRAISE_GAP)
    return tuple(gaps)


def _size(pr: PullRequest) -> tuple[str, str]:
    lines = _lines(pr)
    if lines is None or pr.changed_files is None:
        return "", ""
    if lines >= NOTABLE_LINES and pr.changed_files >= NOTABLE_FILES:
        return "size", f"{lines} lines across {pr.changed_files} files"
    return "", ""


def _review(pr: PullRequest) -> tuple[str, str]:
    if pr.review_comments is None:
        return "", ""
    if pr.review_comments >= REVIEW_COMMENTS and len(pr.reviewers) >= REVIEWERS:
        return "review", f"{pr.review_comments} review comments, {len(pr.reviewers)} reviewers"
    return "", ""


def _linked(pr: PullRequest) -> tuple[str, str]:
    for issue in pr.linked:
        hit = sorted(LINKED_LABELS & _lower(issue.labels))
        if hit:
            return "linked", f"closes a {hit[0]}-labelled issue"
    return "", ""


def _label(pr: PullRequest) -> tuple[str, str]:
    hit = sorted(NOTABLE_LABELS & _lower(pr.labels))
    return ("label", f"labelled {hit[0]}") if hit else ("", "")


def _in_release(pr: PullRequest, release: Release) -> bool:
    notes = release.notes
    if not notes:
        return False
    if re.search(rf"(?<![\w/])#{pr.number}(?!\d)|/pull/{pr.number}(?!\d)", notes):
        return True
    return bool(pr.title) and len(pr.title) >= 8 and pr.title.casefold() in notes.casefold()


def _lines(pr: PullRequest) -> int | None:
    if pr.additions is None or pr.deletions is None:
        return None
    return pr.additions + pr.deletions


def _lower(values: Sequence[str]) -> frozenset[str]:
    return frozenset(v.strip().casefold() for v in values)


def _merged_key(pr: PullRequest) -> float:
    return pr.merged_at.timestamp() if pr.merged_at else 0.0


def review_data_missing(prs: Sequence[PullRequest]) -> bool:
    """True when no checked pull request carried a review-comment count: the review rule was not applied."""
    real = [p for p in prs if not p.pasted]
    return bool(real) and all(p.review_comments is None for p in real)
