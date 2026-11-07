"""Pure: read pull requests, reviews, comments, commits and checks out of raw tool output by their shape
(field names a code-host API returns, or a pasted pull request list), never by trusting the model.

Every value here came out of a tool result (or the user's paste) this turn. Each reader is asked about
one call's output, so it knows what it is looking for.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

REVIEW_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"})
FAILED_CHECKS = frozenset({"failure", "error", "timed_out", "action_required", "startup_failure"})
PENDING_CHECKS = frozenset({"pending", "queued", "in_progress", "expected", "waiting", "requested"})
CONFLICT_STATES = frozenset({"dirty", "conflicting"})


@dataclass(frozen=True)
class PullRequest:
    number: str
    title: str
    author: str
    url: str
    draft: bool
    created_at: str
    updated_at: str
    head_sha: str = ""
    requested: tuple[str, ...] = ()   # individual reviewers requested
    teams: tuple[str, ...] = ()       # team slugs requested
    conflict: bool | None = None      # None: not reported by this output
    changed_lines: int = 0


@dataclass(frozen=True)
class Review:
    author: str
    state: str
    submitted_at: str


@dataclass(frozen=True)
class Comment:
    author: str
    created_at: str


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def objects(value: Any) -> Iterator[Mapping[str, Any]]:
    """Every JSON object at any depth (JSON strings that hold JSON are opened too)."""
    yield from _walk(parse_json(value) if isinstance(value, str) else value, 0)


def _walk(value: Any, depth: int) -> Iterator[Mapping[str, Any]]:
    if depth > 12:
        return
    if isinstance(value, Mapping):
        yield value
        for v in value.values():
            yield from _walk(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, depth + 1)
    elif isinstance(value, str) and value[:1] in "[{" and len(value) < 2_000_000:
        yield from _walk(parse_json(value), depth + 1)


def s(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def login(value: Any) -> str:
    """`{"login": "maya"}`, `{"username": ...}`, `{"name": ...}` or a bare string."""
    if isinstance(value, Mapping):
        return s(value.get("login") or value.get("username") or value.get("slug") or value.get("name"))
    return s(value) if isinstance(value, str) else ""


def _first(o: Mapping[str, Any], *keys: str) -> Any:
    return next((o[k] for k in keys if k in o and o[k] is not None), None)


def _is_pr(o: Mapping[str, Any]) -> bool:
    if "number" not in o or "title" not in o:
        return False
    head = o.get("head")
    return (isinstance(head, Mapping) and bool(head.get("sha"))) or "isDraft" in o or "headRefOid" in o or (
        "draft" in o and ("user" in o or "author" in o))


def pull_requests(text: Any) -> list[PullRequest]:
    out: dict[str, PullRequest] = {}
    for o in objects(text):
        if _is_pr(o) and s(o.get("state") or "open").casefold() == "open":
            pr = _pull_request(o)
            out.setdefault(pr.number, pr)
    return list(out.values())


def _pull_request(o: Mapping[str, Any]) -> PullRequest:
    head = o.get("head") if isinstance(o.get("head"), Mapping) else {}
    requested, teams = _requests(o)
    return PullRequest(
        number=s(o["number"]), title=s(o.get("title")), author=login(_first(o, "user", "author")),
        url=_web_url(o),
        draft=bool(_first(o, "draft", "isDraft")), created_at=s(_first(o, "created_at", "createdAt")),
        updated_at=s(_first(o, "updated_at", "updatedAt")), head_sha=s(head.get("sha") or o.get("headRefOid")),
        requested=requested, teams=teams, conflict=_conflict(o),
        changed_lines=_int(o.get("additions")) + _int(o.get("deletions")))


def _web_url(o: Mapping[str, Any]) -> str:
    """The page a person opens: `html_url`, or a `url` that is a web link (not an API one)."""
    if o.get("html_url"):
        return s(o["html_url"])
    url = s(o.get("url") or o.get("web_url"))
    is_web = url.startswith("http") and ("/pull/" in url or "/merge_requests/" in url)
    return url if is_web else ""


def _requests(o: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    people = [login(r) for r in o.get("requested_reviewers") or () if login(r)]
    teams = [login(t) for t in o.get("requested_teams") or () if login(t)]
    for r in o.get("reviewRequests") or ():  # a pasted list: users have a login, teams a slug or name
        if isinstance(r, Mapping) and r.get("login"):
            people.append(s(r["login"]))
        elif isinstance(r, Mapping) and (r.get("slug") or r.get("name")):
            teams.append(s(r.get("slug") or r.get("name")))
    return tuple(dict.fromkeys(people)), tuple(dict.fromkeys(teams))


def _conflict(o: Mapping[str, Any]) -> bool | None:
    state = s(_first(o, "mergeable_state", "mergeStateStatus")).casefold()
    mergeable = o.get("mergeable")
    if state in CONFLICT_STATES or s(mergeable).casefold() == "conflicting":
        return True
    if state in ("clean", "unstable", "has_hooks", "blocked", "behind") or s(mergeable).casefold() == "mergeable":
        return False
    if mergeable is True:
        return False
    return None


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def requested_reviewers(text: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    people: list[str] = []
    teams: list[str] = []
    for o in objects(text):
        for key, sink in (("users", people), ("requested_reviewers", people), ("teams", teams),
                          ("requested_teams", teams)):
            for item in o.get(key) or () if isinstance(o.get(key), list) else ():
                if login(item):
                    sink.append(login(item))
    return tuple(dict.fromkeys(people)), tuple(dict.fromkeys(teams))


def reviews(text: Any) -> list[Review]:
    out = []
    for o in objects(text):
        state = s(o.get("state")).upper()
        when = s(_first(o, "submitted_at", "submittedAt"))
        if state in REVIEW_STATES and when and _first(o, "user", "author") is not None:
            out.append(Review(login(_first(o, "user", "author")), state, when))
    return out


def comments(text: Any) -> list[Comment]:
    out = []
    for o in objects(text):
        when = s(_first(o, "created_at", "createdAt"))
        if when and "body" in o and _first(o, "user", "author") is not None and "state" not in o:
            out.append(Comment(login(_first(o, "user", "author")), when))
    return out


def commit_dates(text: Any) -> list[str]:
    out = []
    for o in objects(text):
        inner = o.get("commit")
        if isinstance(inner, Mapping) and (o.get("sha") or inner.get("message") is not None):
            who = inner.get("committer") if isinstance(inner.get("committer"), Mapping) else {}
            author = inner.get("author") if isinstance(inner.get("author"), Mapping) else {}
            date = s(who.get("date") or author.get("date"))
            if date:
                out.append(date)
        elif "oid" in o and _first(o, "committedDate", "authoredDate"):
            out.append(s(_first(o, "committedDate", "authoredDate")))
    return out


def check_state(text: Any) -> str:
    """failing / pending / passing from check runs or a combined status; "" when the output has none."""
    found: list[str] = []
    for o in objects(text):
        if "conclusion" in o and ("head_sha" in o or "check_suite" in o or "name" in o):
            found.append(s(o.get("conclusion") or o.get("status")).casefold())
        elif "state" in o and "context" in o:
            found.append(s(o.get("state")).casefold())
        elif "state" in o and isinstance(o.get("statuses"), list) and o["statuses"]:
            found.append(s(o.get("state")).casefold())  # a combined status with no statuses says "pending"
    return summarize_checks(found)


def summarize_checks(states: list[str]) -> str:
    states = [x for x in states if x]
    if not states:
        return ""
    if any(x in FAILED_CHECKS for x in states):
        return "failing"
    if any(x in PENDING_CHECKS for x in states):
        return "pending"
    return "passing"


def parse_time(value: str) -> datetime | None:
    text = (value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
