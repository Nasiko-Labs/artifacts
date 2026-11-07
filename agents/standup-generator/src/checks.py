"""Pure: turn a read step's JSON answer into checked records.

The model only transcribes. A record is kept only when its URL and the person it names appear in THIS
step's tool output and its times parse. Titles, comment text, branch names, statuses, labels and
review states are kept only when they appear verbatim in the tool output; otherwise they are left out
(the line shows the reference instead). Flags (draft, failing checks, merge conflict) are believed only
when the tool output carries the matching word. At most MAX_RECORDS per kind; the cut is reported.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from records import Comment, Commit, Identity, Issue, PullRequest, Records, Review
from window import parse_time

MAX_RECORDS = 100
REVIEW_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"})
DONE_WORDS = ("done", "completed", "complete", "closed", "resolved", "shipped", "released", "fixed")
STARTED_WORDS = ("progress", "started", "review", "doing", "development", "active", "testing", "qa")
_FAILING = ("fail", "error", "timed_out", "timed out", "cancelled")
_PASSING = ("success", "passed", "passing")
_CONFLICT = ("conflict", "dirty")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class Seen:
    """This step's raw tool output, the only source a record may come from."""

    text: str

    def has(self, value: object) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        needle = value.strip()
        return needle in self.text or json.dumps(needle)[1:-1] in self.text

    def has_number(self, value: int) -> bool:
        return re.search(rf"(?<![\d.]){value}(?![\d.])", self.text) is not None

    def mentions(self, words: tuple[str, ...]) -> bool:
        lowered = self.text.casefold()
        return any(w in lowered for w in words)


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


def identity(raw: Mapping[str, Any], prefix: str, seen: Seen) -> Identity | None:
    found = Identity(handle=seen_str(raw.get(prefix), seen), name=seen_str(raw.get(f"{prefix}_name"), seen),
                     email=seen_str(raw.get(f"{prefix}_email"), seen))
    if found.is_empty() or _is_bot(found):
        return None
    return found


def code_records(answer: Mapping[str, Any], seen: Seen) -> Records:
    prs = _unique((_pr(raw, seen) for raw in items(answer, "prs")), lambda p: p.url)
    commits = _unique((_commit(raw, seen) for raw in items(answer, "commits")), lambda c: c.sha)
    cuts = tuple(what for what, rows in (("pull requests", prs), ("commits", commits)) if len(rows) > MAX_RECORDS)
    return Records(prs=prs[:MAX_RECORDS], commits=commits[:MAX_RECORDS], cuts=cuts)


def tracker_records(answer: Mapping[str, Any], seen: Seen) -> Records:
    issues = _unique((_issue(raw, seen) for raw in items(answer, "issues")), lambda i: i.key)
    return Records(issues=issues[:MAX_RECORDS], cuts=("issues",) if len(issues) > MAX_RECORDS else ())


def _pr(raw: Mapping[str, Any], seen: Seen) -> PullRequest | None:
    url, author, created = raw.get("url"), identity(raw, "author", seen), seen_time(raw.get("created_at"), seen)
    if not seen.has(url) or author is None or created is None:
        return None
    merged = seen_time(raw.get("merged_at"), seen)
    state = "merged" if merged else ("closed" if str(raw.get("state", "")).casefold() == "closed" else "open")
    number = raw.get("number")
    number = number if isinstance(number, int) and not isinstance(number, bool) and seen.has_number(number) else None
    return PullRequest(
        url=url.strip(), author=author, created_at=created, title=seen_str(raw.get("title"), seen),
        repo=seen_str(raw.get("repo"), seen), number=number, state=state,
        draft=raw.get("draft") is True and seen.mentions(("draft",)), merged_at=merged,
        last_push_at=seen_time(raw.get("last_push_at"), seen), branch=seen_str(raw.get("branch"), seen),
        checks=_checks(raw.get("checks"), seen), conflict=raw.get("conflict") is True and seen.mentions(_CONFLICT),
        reviews=_reviews(raw, seen, author), comment=_comment(raw.get("latest_comment"), seen))


def _reviews(raw: Mapping[str, Any], seen: Seen, author: Identity) -> tuple[Review, ...]:
    out = []
    for r in items(raw, "reviews"):
        reviewer, at = identity(r, "reviewer", seen), seen_time(r.get("submitted_at"), seen)
        state = str(r.get("state") or "").strip().upper()
        if reviewer is None or at is None or state not in REVIEW_STATES or not seen.mentions((state.casefold(),)):
            continue
        if reviewer.handle and reviewer.handle.casefold() == author.handle.casefold():
            continue  # a self-review is not a review
        out.append(Review(reviewer, state, at))
    return tuple(out)


def _checks(value: object, seen: Seen) -> str:
    word = str(value or "").casefold()
    if word.startswith("fail") and seen.mentions(_FAILING):
        return "failing"
    if word.startswith("pass") and seen.mentions(_PASSING):
        return "passing"
    return "unknown"


def _commit(raw: Mapping[str, Any], seen: Seen) -> Commit | None:
    url, author, at = raw.get("url"), identity(raw, "author", seen), seen_time(raw.get("authored_at"), seen)
    sha = seen_str(raw.get("sha"), seen)
    if not seen.has(url) or author is None or at is None or not sha:
        return None
    return Commit(sha=sha, url=url.strip(), author=author, authored_at=at, branch=seen_str(raw.get("branch"), seen),
                  message=seen_str(raw.get("message"), seen))


def _issue(raw: Mapping[str, Any], seen: Seen) -> Issue | None:
    url, key, assignee = raw.get("url"), seen_str(raw.get("key"), seen), identity(raw, "assignee", seen)
    if not seen.has(url) or not key or assignee is None:
        return None
    status = seen_str(raw.get("status"), seen)
    labels = tuple(seen_str(v, seen) for v in _list(raw.get("labels")) if seen_str(v, seen))
    blocked_by = tuple(seen_str(v, seen) for v in _list(raw.get("blocked_by")) if seen_str(v, seen))
    return Issue(key=key, url=url.strip(), assignee=assignee, status=status or "unknown",
                 category=category(status, seen_str(raw.get("status_category"), seen), labels),
                 title=seen_str(raw.get("title"), seen), labels=labels,
                 completed_at=seen_time(raw.get("completed_at"), seen),
                 updated_at=seen_time(raw.get("updated_at"), seen), blocked_by=blocked_by,
                 comment=_comment(raw.get("latest_comment"), seen))


def category(status: str, status_category: str, labels: tuple[str, ...]) -> str:
    """done | blocked | started | other, from the tracker's own words (never from the model's judgment)."""
    words = f"{status} {status_category}".casefold()
    if "block" in words or any("block" in label.casefold() for label in labels):
        return "blocked"
    if any(re.search(rf"\b{w}\b", words) for w in DONE_WORDS):
        return "done"
    if any(w in words for w in STARTED_WORDS):
        return "started"
    return "other"


def _comment(raw: object, seen: Seen) -> Comment | None:
    if not isinstance(raw, Mapping):
        return None
    body = seen_str(raw.get("body"), seen)
    return Comment(body, seen_time(raw.get("at"), seen)) if body else None


def seen_str(value: object, seen: Seen) -> str:
    return value.strip() if seen.has(value) else ""


def seen_time(value: object, seen: Seen):
    """A time the tool returned: the raw value (or its date part) must appear in the tool output."""
    at = parse_time(value)
    if at is None:
        return None
    raw = str(value).strip()
    return at if seen.has(raw) or seen.has(raw[:10]) or str(int(at.timestamp())) in seen.text else None


def _is_bot(found: Identity) -> bool:
    handle = found.handle.casefold()
    return handle.endswith(("[bot]", "-bot")) or handle == "bot"


def _unique(rows, key) -> tuple:
    kept: dict[str, Any] = {}
    for row in rows:
        if row is not None:
            kept.setdefault(key(row), row)
    return tuple(kept.values())


def _list(value: object) -> list:
    return value if isinstance(value, list) else []
