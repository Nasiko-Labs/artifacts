"""Code host evidence, checked in code: commit authors of the top 5 matching files (at most 30 commits a
file), authors of matching pull requests, and their reviewers.

Commit authorship per path is the stand-in for line-level blame (the code host tools return commit
lists, not blame), and the reply says so. Every author, path, title, date and link must appear in this
step's tool output; an item that fails is dropped and counted."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import keywords
from models import Evidence, Who
from verify import appears_in, normalise_text, parse_day, safe_link, text_of

TOP_FILES = 5
COMMITS_PER_FILE = 30


@dataclass(frozen=True)
class Read:
    evidence: tuple[Evidence, ...]
    dropped: int = 0
    me: Who | None = None


def evidence_from(data: Mapping[str, Any], seen_text: str, terms: Sequence[str]) -> Read:
    seen = normalise_text(seen_text)
    top = _top_files(data.get("files"), seen)
    commits, dropped_commits = _commits(data.get("commits"), seen, top)
    prs, dropped_prs = _pull_requests(data.get("prs"), seen, terms)
    return Read((*commits, *prs), dropped_commits + dropped_prs, who_of(data.get("me"), seen))


def _top_files(raw: Any, seen: str) -> frozenset[str] | None:
    """The 5 matching files with the most matches (ties by path), or None when no file list was given."""
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    files = [(text_of(r.get("path")), _int(r.get("matches"))) for r in rows]
    backed = [(path, count) for path, count in files if path and appears_in(path, seen)]
    if not backed:
        return None
    ranked = sorted(backed, key=lambda f: (-f[1], f[0]))
    return frozenset(path for path, _ in ranked[:TOP_FILES])


def _commits(raw: Any, seen: str, top: frozenset[str] | None) -> tuple[tuple[Evidence, ...], int]:
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    kept: list[Evidence] = []
    dropped = 0
    for row in rows:
        path = text_of(row.get("path"))
        if top is not None and path not in top:
            continue  # outside the top files: not evidence, not a failed check
        who = Who(text_of(row.get("author_name")), text_of(row.get("author_email")), text_of(row.get("author_login")))
        item = _checked("commit", who, path, row.get("date"), row, seen)
        if item is None:
            dropped += 1
            continue
        kept.append(item)
    return _cap_per_file(kept), dropped


def _cap_per_file(items: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """The latest 30 commits of each file."""
    by_path: dict[str, list[Evidence]] = {}
    for item in sorted(items, key=lambda e: e.day, reverse=True):
        by_path.setdefault(item.detail, []).append(item)
    return tuple(e for group in by_path.values() for e in group[:COMMITS_PER_FILE])


def _pull_requests(raw: Any, seen: str, terms: Sequence[str]) -> tuple[tuple[Evidence, ...], int]:
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    kept: list[Evidence] = []
    dropped = 0
    for row in rows:
        title = text_of(row.get("title"))
        if keywords.hits(title, terms) == 0:
            continue  # a search neighbour, not about the topic
        author = Who(text_of(row.get("author_name")), "", text_of(row.get("author_login")))
        item = _checked("pr", author, title, row.get("date"), row, seen)
        if item is None:
            dropped += 1
            continue
        kept.append(item)
        reviews, failed = _reviews(row.get("reviews"), item, seen)
        kept.extend(reviews)
        dropped += failed
    return tuple(kept), dropped


def _reviews(raw: Any, pr: Evidence, seen: str) -> tuple[list[Evidence], int]:
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    kept: list[Evidence] = []
    dropped = 0
    for row in rows:
        who = backed_who(Who(text_of(row.get("name")), "", text_of(row.get("login"))), seen)
        if who is None:
            dropped += 1
            continue
        if _same(who, pr.who):
            continue  # an author's reply on their own pull request is not a review
        day = parse_day(row.get("date")) if appears_in(text_of(row.get("date")), seen) else None
        state = text_of(row.get("state")).upper()
        kept.append(Evidence("review", who, pr.detail, day or pr.day, pr.link, pr.repo, state=state))
    return kept, dropped


def _checked(kind: str, who: Who, detail: str, raw_date: Any, row: Mapping[str, Any], seen: str) -> Evidence | None:
    date_text = text_of(raw_date)
    day = parse_day(raw_date)
    person = backed_who(who, seen)
    if person is None or not detail or not appears_in(detail, seen):
        return None
    if day is None or not appears_in(date_text, seen):
        return None
    repo = text_of(row.get("repo"))
    return Evidence(kind, person, detail, day, safe_link(text_of(row.get("link")), seen),
                    repo if appears_in(repo, seen) else "")


def backed_who(who: Who, seen: str) -> Who | None:
    """The person with only the fields that appear in the tool output; None when no field does."""
    kept = Who(*(v if v and appears_in(v, seen) else "" for v in (who.name, who.email, who.handle)))
    return None if kept.is_empty() else kept


def who_of(raw: Any, seen: str) -> Who | None:
    """The signed-in user ("me"), only when backed by tool output."""
    if not isinstance(raw, Mapping):
        return None
    who = Who(text_of(raw.get("name")), text_of(raw.get("email")), text_of(raw.get("login") or raw.get("handle")))
    return backed_who(who, seen)


def _same(a: Who, b: Who) -> bool:
    return bool((a.handle and a.handle.casefold() == b.handle.casefold())
                or (a.email and a.email.casefold() == b.email.casefold()))


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
