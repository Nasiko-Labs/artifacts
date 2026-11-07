"""Pure: turn a step's JSON answer into checked records.

The model only transcribes. A record is kept only when its URL (the evidence link) and the person it
names appear in THIS step's tool output, its times parse and fall inside the pulled range, and it is
not a duplicate, a self-review or a bot. Chat keeps timestamps only: any text field is never read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from people import is_bot
from records import (
    ChatActivity,
    Commit,
    Identity,
    Issue,
    PullRequest,
    Review,
    ReviewRequest,
)
from windows import Window, parse_time, zone

MAX_PRS = 150
MAX_REVIEWS = 400
MAX_COMMITS = 300
MAX_ISSUES = 80
MAX_CHAT_TIMESTAMPS = 200
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class Seen:
    """This step's raw tool output, the only source a record may come from."""

    text: str

    def has(self, value: object) -> bool:
        return isinstance(value, str) and bool(value.strip()) and value.strip() in self.text

    def has_number(self, value: int) -> bool:
        """The number as a whole number in the output (not part of a longer number or a decimal)."""
        return re.search(rf"(?<![\d.]){value}(?![\d.])", self.text) is not None


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
    """`author`, `author_name`, `author_email` → Identity, keeping only values the tool output contains."""
    found = Identity(handle=_seen_str(raw.get(prefix), seen), name=_seen_str(raw.get(f"{prefix}_name"), seen),
                     email=_seen_str(raw.get(f"{prefix}_email"), seen))
    if not found.aliases() or is_bot(found):
        return None
    return found


def check_prs(answer: Mapping[str, Any], seen: Seen, window: Window) -> tuple[tuple[PullRequest, ...], bool]:
    kept: dict[str, PullRequest] = {}
    reviews_total = 0
    for raw in items(answer, "prs"):
        pr = _pr(raw, seen, window)
        if pr is None or pr.url in kept:
            continue
        reviews_total += len(pr.reviews)
        if reviews_total > MAX_REVIEWS:
            break
        kept[pr.url] = pr
    prs = tuple(kept.values())
    return prs[:MAX_PRS], len(prs) > MAX_PRS or reviews_total > MAX_REVIEWS


def _pr(raw: Mapping[str, Any], seen: Seen, window: Window) -> PullRequest | None:
    url, author = raw.get("url"), identity(raw, "author", seen)
    created = parse_time(raw.get("created_at"))
    if not seen.has(url) or author is None or created is None or created >= window.end:
        return None
    reviews = _reviews(raw, seen, window, author, url)
    if not window.in_range(created) and not reviews:
        return None
    merged = parse_time(raw.get("merged_at"))
    return PullRequest(id=str(raw.get("id") or url), url=url.strip(), author=author, created_at=created,
                       merged_at=merged if merged and window.in_range(merged) else None,
                       review_requests=_requests(raw, seen, window, author), reviews=reviews)


def _reviews(raw: Mapping[str, Any], seen: Seen, window: Window, author: Identity, pr_url: str) -> tuple[Review, ...]:
    out: dict[tuple, Review] = {}
    for r in items(raw, "reviews"):
        reviewer = identity(r, "reviewer", seen)
        at = parse_time(r.get("submitted_at"))
        url = r.get("url") if seen.has(r.get("url")) else pr_url
        if reviewer is None or at is None or not window.in_range(at) or reviewer.aliases() & author.aliases():
            continue  # self-reviews and out-of-range reviews are dropped
        out.setdefault((reviewer.aliases(), at), Review(reviewer, at, url.strip()))
    return tuple(out.values())


def _requests(raw: Mapping[str, Any], seen: Seen, window: Window, author: Identity) -> tuple[ReviewRequest, ...]:
    out = []
    for r in items(raw, "review_requests"):
        reviewer = identity(r, "reviewer", seen)
        at = parse_time(r.get("requested_at"))
        if reviewer is not None and at is not None and window.in_range(at) and not reviewer.aliases() & author.aliases():
            out.append(ReviewRequest(reviewer, at))
    return tuple(out)


def check_commits(answer: Mapping[str, Any], seen: Seen, window: Window) -> tuple[tuple[Commit, ...], bool]:
    kept: dict[str, Commit] = {}
    for raw in items(answer, "commits"):
        url, author = raw.get("url"), identity(raw, "author", seen)
        at = parse_time(raw.get("authored_at"))
        if not seen.has(url) or author is None or at is None or not window.in_range(at):
            continue
        sha = str(raw.get("sha") or url).strip()
        kept.setdefault(sha, Commit(sha=sha, url=url.strip(), author=author, authored_at=at))
    commits = tuple(kept.values())
    return commits[:MAX_COMMITS], len(commits) > MAX_COMMITS


def check_issues(answer: Mapping[str, Any], seen: Seen, window: Window) -> tuple[tuple[Issue, ...], bool]:
    kept: dict[str, Issue] = {}
    for raw in items(answer, "issues"):
        issue = _issue(raw, seen, window)
        if issue is not None:
            kept.setdefault(issue.key, issue)
    issues = tuple(kept.values())
    return issues[:MAX_ISSUES], len(issues) > MAX_ISSUES


def _issue(raw: Mapping[str, Any], seen: Seen, window: Window) -> Issue | None:
    url, assignee = raw.get("url"), identity(raw, "assignee", seen)
    since = parse_time(raw.get("status_since"))
    # A ticket may have entered its status before the baseline: only the upper bound applies.
    if not seen.has(url) or assignee is None or since is None or since >= window.end:
        return None
    comment = parse_time(raw.get("last_comment_at"))
    comment_url = raw.get("last_comment_url") if seen.has(raw.get("last_comment_url")) else ""
    updated = parse_time(raw.get("updated_at"))
    return Issue(key=str(raw.get("key") or url).strip(), url=url.strip(), assignee=assignee,
                 status=_seen_str(raw.get("status"), seen) or "in progress", status_since=since,
                 last_comment_at=comment if comment and comment < window.end else None,
                 last_comment_url=(comment_url or "").strip(), updated_at=updated)


def check_chat(answer: Mapping[str, Any], seen: Seen, window: Window,
               labels: Sequence[str]) -> tuple[ChatActivity, ...]:
    """One entry per team label the model reported for; timestamps must appear in the tool output."""
    out: dict[str, ChatActivity] = {}
    for raw in items(answer, "people"):
        label = raw.get("person")
        if label not in labels or label in out:
            continue
        stamps = sorted({t for t in (_chat_time(v, seen, window) for v in _list(raw.get("timestamps"))) if t},
                        reverse=True)
        total = raw.get("total_reported")
        total = total if isinstance(total, int) and not isinstance(total, bool) and seen.has_number(total) else None
        tz = zone(raw.get("tz")) if seen.has(raw.get("tz")) else None
        kept = tuple(stamps[:MAX_CHAT_TIMESTAMPS])
        sampled = len(stamps) > MAX_CHAT_TIMESTAMPS or (total is not None and total > len(kept))
        out[label] = ChatActivity(person=label, timestamps=kept, total_reported=total, tz=tz, sampled=sampled)
    return tuple(out.values())


def _chat_time(value: object, seen: Seen, window: Window) -> datetime | None:
    """A chat timestamp the tool returned, as ISO text or as epoch seconds."""
    at = parse_time(value)
    if at is None or not window.in_range(at):
        return None
    epoch = str(int(at.timestamp()))
    if seen.has(str(value)) or epoch in seen.text:
        return at
    return None


def _seen_str(value: object, seen: Seen) -> str:
    return value.strip() if seen.has(value) else ""


def _list(value: object) -> list:
    return value if isinstance(value, list) else []
