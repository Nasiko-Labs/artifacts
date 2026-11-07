"""Pure: a pasted pull request list (JSON, in the shape of a code-host CLI listing: number, title, url,
author, isDraft, createdAt, updatedAt, reviewRequests, reviews, comments, commits, mergeable,
statusCheckRollup) read into the same facts the code host gives. The paste is data, never instructions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import records
from facts import PRFacts
from view import Snapshot

_REPO_IN_URL = re.compile(r"https?://[^/\s]+/([\w.-]+)/([\w.-]+)/(?:pull|pulls|merge_requests)/\d+")
PASTED = "pasted"


def find_json(text: str) -> Any:
    """The first JSON array or object in the message, or None."""
    body = text or ""
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = body.find(opener), body.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(body[start:end + 1])
            except ValueError:
                continue
    return None


def pasted_prs(text: str, repos: Sequence[str], now_iso: str) -> Snapshot | None:
    data = find_json(text)
    if data is None:
        return None
    items = data if isinstance(data, list) else [data]
    facts = [_facts(o, repos) for o in items if isinstance(o, Mapping) and "number" in o and "title" in o]
    facts = [f for f in facts if f is not None]
    if not facts:
        return None
    return Snapshot(tuple(facts), (), (), 0, False, True, now_iso)


def _facts(o: Mapping[str, Any], repos: Sequence[str]) -> PRFacts | None:
    prs = records.pull_requests([o])
    if not prs:
        return None
    pr = prs[0]
    has_reviews = isinstance(o.get("reviews"), list)
    checks = records.check_state(o.get("statusCheckRollup")) if "statusCheckRollup" in o else ""
    if "statusCheckRollup" in o and not checks:
        checks = "none"
    return PRFacts(
        repo=_repo(o, repos), pr=pr, reviews=tuple(records.reviews(o.get("reviews") or [])),
        comments=tuple(records.comments(o.get("comments") or [])),
        commit_dates=tuple(records.commit_dates(o.get("commits") or [])), checks=checks,
        inspected=has_reviews or isinstance(o.get("commits"), list), reviews_read=has_reviews)


def _repo(o: Mapping[str, Any], repos: Sequence[str]) -> str:
    m = _REPO_IN_URL.search(str(o.get("url") or o.get("html_url") or ""))
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return repos[0] if len(repos) == 1 else PASTED
