"""Pure: read runs, jobs, logs, repositories, branches, files, commits, issues, search hits and write
results out of raw tool output by their shape (the field names a CI or code-host API returns).

Code reads the facts itself instead of trusting the model to copy ids: every value here came out of a
tool result this turn.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_LOG_MIN_LINES = 3
_ARCHIVE = re.compile(r"https?://\S+(?:\.zip|/logs?\b|logs?\?|/archive)\S*", re.IGNORECASE)


@dataclass(frozen=True)
class Run:
    run_id: str
    workflow: str
    head_sha: str
    branch: str
    created_at: str
    conclusion: str
    status: str = ""
    url: str = ""
    attempt: int = 1
    event: str = ""


@dataclass(frozen=True)
class Job:
    job_id: str
    run_id: str
    name: str
    conclusion: str
    head_sha: str = ""
    attempt: int = 0     # 0: the listing did not say


@dataclass(frozen=True)
class FileContent:
    path: str
    sha: str
    text: str


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str


@dataclass(frozen=True)
class Issue:
    number: str
    title: str
    state: str
    url: str = ""
    body: str = ""


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def objects(text: str) -> Iterator[Mapping[str, Any]]:
    """Every JSON object at any depth of a tool output (JSON strings that hold JSON are opened too)."""
    yield from _walk(parse_json(text), 0)


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


def _int(value: Any, default: int) -> int:
    text = s(value)
    return int(text) if text.isdigit() else default


def runs(text: str) -> list[Run]:
    out = []
    for o in objects(text):
        if "id" in o and "head_sha" in o and "run_id" not in o and ("workflow_id" in o or "run_number" in o
                                                                     or "run_attempt" in o):
            out.append(Run(s(o["id"]), s(o.get("name") or o.get("display_title")), s(o["head_sha"]),
                           s(o.get("head_branch")), s(o.get("created_at") or o.get("run_started_at")),
                           s(o.get("conclusion")), s(o.get("status")), s(o.get("html_url")),
                           _int(o.get("run_attempt"), 1), s(o.get("event"))))
    return out


def jobs(text: str) -> list[Job]:
    out = []
    for o in objects(text):
        if "id" in o and "run_id" in o and ("steps" in o or "conclusion" in o):
            out.append(Job(s(o["id"]), s(o["run_id"]), s(o.get("name")), s(o.get("conclusion")),
                           s(o.get("head_sha")), _int(o.get("run_attempt"), 0)))
    return out


def default_branch(text: str) -> str:
    return next((s(o["default_branch"]) for o in objects(text) if s(o.get("default_branch"))), "")


def branch_head(text: str, name: str) -> str:
    """The head commit of the named branch, from a branch object (`{"name", "commit": {"sha"}}`)."""
    for o in objects(text):
        commit = o.get("commit")
        if s(o.get("name")) == name and isinstance(commit, Mapping) and s(commit.get("sha")):
            return s(commit["sha"])
    return ""


def files(text: str) -> list[FileContent]:
    out = []
    for o in objects(text):
        path, sha = s(o.get("path")), s(o.get("sha"))
        if not path or "content" not in o and "decoded_content" not in o:
            continue
        body = _decoded(o)
        if body is not None:
            out.append(FileContent(path, sha, body))
    return out


def _decoded(o: Mapping[str, Any]) -> str | None:
    if isinstance(o.get("decoded_content"), str):
        return o["decoded_content"]
    content = o.get("content")
    if not isinstance(content, str):
        return None
    if s(o.get("encoding")).casefold() != "base64":
        return content
    try:
        return base64.b64decode(content.replace("\n", ""), validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def search_paths(text: str) -> list[str]:
    """Paths of code-search hits (`{"path", "repository"|"sha"|"html_url"}` without a body)."""
    out = []
    for o in objects(text):
        if s(o.get("path")) and "content" not in o and ("repository" in o or "html_url" in o or "sha" in o):
            out.append(s(o["path"]))
    return list(dict.fromkeys(out))


def commits(text: str) -> list[Commit]:
    out = []
    for o in objects(text):
        inner = o.get("commit")
        if isinstance(o.get("sha"), str) and isinstance(inner, Mapping):
            committer = inner.get("committer") if isinstance(inner.get("committer"), Mapping) else {}
            author = inner.get("author") if isinstance(inner.get("author"), Mapping) else {}
            out.append(Commit(o["sha"], s(committer.get("date") or author.get("date"))))
    return out


def issues(text: str) -> list[Issue]:
    out = []
    for o in objects(text):
        if "number" in o and "title" in o and "state" in o and "head" not in o and "pull_request" not in o:
            out.append(Issue(s(o["number"]), s(o["title"]), s(o["state"]), s(o.get("html_url")), s(o.get("body"))))
    return out


def created_number(text: str) -> tuple[str, str]:
    """(number, link) of an issue or pull request a write just created."""
    for o in objects(text):
        if s(o.get("number")).isdigit():
            return s(o["number"]), s(o.get("html_url"))
    return "", ""


def created_ref(text: str) -> str:
    """The branch a create-branch write reports (`refs/heads/x` → `x`), or ""."""
    for o in objects(text):
        ref = s(o.get("ref"))
        if ref:
            return ref.removeprefix("refs/heads/")
    return ""


def committed_path(text: str) -> str:
    for o in objects(text):
        content = o.get("content")
        if isinstance(content, Mapping) and s(content.get("path")):
            return s(content["path"])
    return ""


def log_text(text: str) -> str:
    """The job log in a tool output: the output itself when it is plain text, else its longest
    multi-line string field. Empty when the output holds no log."""
    data = parse_json(text)
    if data is None:
        return text if text.count("\n") + 1 >= _LOG_MIN_LINES else ""
    best = max(_strings(data), key=len, default="")
    return best if best.count("\n") + 1 >= _LOG_MIN_LINES else ""


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def annotation_lines(text: str) -> list[str]:
    return [f"{s(o.get('path'))}: {s(o.get('message'))}" for o in objects(text)
            if "annotation_level" in o and "message" in o]


def archive_url(text: str) -> str:
    m = _ARCHIVE.search(text or "")
    return m.group(0).rstrip('",}') if m else ""


def parse_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def mentioned_ids(arguments: Mapping[str, Any]) -> frozenset[str]:
    """Every scalar argument value as text: which run/job a tool call was about."""
    found: set[str] = set()
    for v in arguments.values():
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            found.add(str(v))
        elif isinstance(v, list):
            found.update(str(x) for x in v if isinstance(x, (str, int)))
    return frozenset(found)
