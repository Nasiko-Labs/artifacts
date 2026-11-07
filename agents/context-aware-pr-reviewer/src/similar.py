"""Pure: similar merged pull requests, ranked in code by the Jaccard overlap of changed file paths with
this pull request, and the review feedback they received (bots excluded).

Candidates come only from this turn's tool output: merged pull requests listed or searched, the files a
candidate changed (from a file listing about that number), and commits on a path whose message names a
pull request (`#N`), which marks it merged and as having changed that path."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import records
from fence import Call

MIN_OVERLAP = 0.25
TOP = 3
MAX_COMMENTS_PER_PR = 6
_NUMBER_KEYS = ("pull_number", "pr_number", "number", "pull_request_number", "pullNumber", "issue_number")
_PATH_KEYS = ("path", "file", "file_path", "filepath")
_PR_IN_MESSAGE = re.compile(r"(?:\(#|pull request #|Merge pull request #|\s#)(\d{1,7})\b")


@dataclass(frozen=True)
class Similar:
    number: str
    title: str
    merged_at: str
    overlap: float
    files: tuple[str, ...]

    def ref(self, repo_full: str) -> str:
        return f"{repo_full}#{self.number}"


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _number_of(arguments: Mapping) -> str:
    for key in _NUMBER_KEYS:
        value = records.s(arguments.get(key)).lstrip("#")
        if value.isdigit():
            return value
    return ""


def _path_of(arguments: Mapping) -> str:
    return next((records.s(arguments.get(k)) for k in _PATH_KEYS if arguments.get(k)), "")


def candidates(calls: Iterable[Call], current: str) -> dict[str, tuple[str, str, set[str]]]:
    """number → (title, merged_at, changed paths) for every merged pull request seen this step."""
    merged: dict[str, tuple[str, str]] = {}
    paths: dict[str, set[str]] = {}
    calls = tuple(calls)
    for c in calls:
        for p in records.merged_pull_requests(c.text):
            merged.setdefault(p.number, (p.title, p.merged_at))
        path = _path_of(c.arguments)
        if path:
            for message in records.commit_messages(c.text):
                for number in _PR_IN_MESSAGE.findall(" " + message.splitlines()[0] if message else ""):
                    merged.setdefault(number, (message.splitlines()[0], ""))
                    paths.setdefault(number, set()).add(path)
    for c in calls:
        number = _number_of(c.arguments)
        if number:
            listed = {f.path for f in records.files(c.text)}
            if listed:
                paths[number] = paths.get(number, set()) | listed
    return {n: (t, at, paths.get(n, set())) for n, (t, at) in merged.items() if n != current and paths.get(n)}


def rank(calls: Iterable[Call], current: str, changed: Iterable[str]) -> tuple[Similar, ...]:
    mine = set(changed)
    scored = [Similar(n, title, at, round(jaccard(mine, files), 2), tuple(sorted(files)))
              for n, (title, at, files) in candidates(calls, current).items()]
    kept = [s for s in scored if jaccard(mine, s.files) >= MIN_OVERLAP]
    kept.sort(key=lambda s: (-jaccard(mine, s.files), _neg(s.merged_at), int(s.number) * -1))
    return tuple(kept[:TOP])


def _neg(text: str) -> tuple:
    """Sort key for newest first on an ISO timestamp (empty sorts last)."""
    return tuple(-ord(ch) for ch in text) if text else (1,)


def precedent(calls: Iterable[Call], numbers: Iterable[str]) -> dict[str, tuple[records.ReviewComment, ...]]:
    """number → its human review comments (verbatim, at most MAX_COMMENTS_PER_PR)."""
    wanted = set(numbers)
    out: dict[str, list[records.ReviewComment]] = {}
    for c in calls:
        number = _number_of(c.arguments)
        if number not in wanted:
            continue
        for comment in records.review_comments(c.text):
            bucket = out.setdefault(number, [])
            if comment.is_bot or not comment.body or comment in bucket or len(bucket) >= MAX_COMMENTS_PER_PR:
                continue
            bucket.append(comment)
    return {n: tuple(v) for n, v in out.items()}
