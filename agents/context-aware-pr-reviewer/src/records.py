"""Pure: read pull requests, changed files, commits, tickets, comments and merged pull requests out of
raw tool output by their shape (field names a code host or tracker API returns), plus the verbatim
checks. Code reads the facts itself instead of trusting the model to copy them: every value here came
out of a tool result this turn."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from diffing import FilePatch, parse_unified

_BOT_TYPES = frozenset({"bot"})


@dataclass(frozen=True)
class PullRequest:
    number: str
    title: str
    body: str
    head_ref: str
    base_ref: str
    url: str = ""
    merged_at: str = ""
    author: str = ""


@dataclass(frozen=True)
class Ticket:
    key: str
    title: str
    description: str
    url: str = ""
    comments: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        parts = [self.title, self.description, *self.comments]
        return "\n\n".join(p for p in parts if p)


@dataclass(frozen=True)
class ReviewComment:
    author: str
    body: str
    path: str = ""
    is_bot: bool = False


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


def _login(user: Any) -> tuple[str, bool]:
    if not isinstance(user, Mapping):
        return "", False
    login = s(user.get("login") or user.get("username") or user.get("name") or user.get("displayName"))
    is_bot = s(user.get("type")).casefold() in _BOT_TYPES or login.casefold().endswith(("[bot]", "-bot"))
    return login, is_bot


def pull_requests(text: str) -> list[PullRequest]:
    out = []
    for o in objects(text):
        head, base = o.get("head"), o.get("base")
        if "number" in o and isinstance(head, Mapping) and isinstance(base, Mapping):
            out.append(PullRequest(s(o["number"]), s(o.get("title")), s(o.get("body")), s(head.get("ref")),
                                   s(base.get("ref")), s(o.get("html_url")), s(o.get("merged_at")),
                                   _login(o.get("user"))[0]))
    return out


def merged_pull_requests(text: str) -> list[PullRequest]:
    """Merged pull requests in a listing or a search result (a search item carries `pull_request`)."""
    out = [p for p in pull_requests(text) if p.merged_at]
    for o in objects(text):
        inner = o.get("pull_request")
        if "number" in o and isinstance(inner, Mapping) and inner.get("merged_at"):
            out.append(PullRequest(s(o["number"]), s(o.get("title")), s(o.get("body")), "", "", s(o.get("html_url")),
                                   s(inner.get("merged_at")), _login(o.get("user"))[0]))
    return out


def commit_messages(text: str) -> list[str]:
    out = []
    for o in objects(text):
        inner = o.get("commit")
        if isinstance(o.get("sha"), str) and isinstance(inner, Mapping) and "message" in inner:
            out.append(s(inner.get("message")))
    return out


def files(text: str) -> list[FilePatch]:
    """Changed files from a file listing (`filename` + `patch`), or from a whole unified diff."""
    out = []
    for o in objects(text):
        if isinstance(o.get("filename"), str) and ("patch" in o or "status" in o or "additions" in o):
            out.append(FilePatch(o["filename"], s(o.get("patch")), s(o.get("status")) or "modified",
                                 s(o.get("previous_filename"))))
    if out:
        return out
    diff = text if "diff --git " in (text or "") and parse_json(text) is None else _longest_diff(text)
    return list(parse_unified(diff)) if diff else []


def _longest_diff(text: str) -> str:
    best = ""
    for o in objects(text):
        for v in o.values():
            if isinstance(v, str) and "diff --git " in v and len(v) > len(best):
                best = v
    return best


def ticket(text: str, ref: str) -> Ticket | None:
    """The ticket `ref` (a tracker key, or `#N` for a code-host issue) in a tool output."""
    for o in objects(text):
        if ref.startswith("#"):
            if s(o.get("number")) == ref[1:] and "title" in o and "head" not in o:
                return Ticket(ref, s(o.get("title")), s(o.get("body")), s(o.get("html_url")))
            continue
        key = s(o.get("identifier") or o.get("key")).upper()
        if key != ref.upper():
            continue
        f = o.get("fields") if isinstance(o.get("fields"), Mapping) else {}
        title = s(o.get("title") or f.get("summary") or o.get("summary"))
        description = flatten(o.get("description") if "description" in o else f.get("description"))
        return Ticket(ref.upper(), title, description, s(o.get("url") or o.get("self")))
    return None


def ticket_comments(text: str) -> list[str]:
    out = []
    for o in objects(text):
        if "body" in o and ("author" in o or "user" in o or "created_at" in o or "createdAt" in o) \
                and "head" not in o and "title" not in o:
            is_bot = _login(o.get("user") or o.get("author"))[1]
            body = flatten(o.get("body"))
            if body and not is_bot:
                out.append(body)
    return out


def review_comments(text: str) -> list[ReviewComment]:
    out = []
    for o in objects(text):
        if isinstance(o.get("body"), str) and isinstance(o.get("user"), Mapping) and "title" not in o:
            login, is_bot = _login(o.get("user"))
            out.append(ReviewComment(login, o["body"].strip(), s(o.get("path")), is_bot))
    return out


def flatten(value: Any) -> str:
    """Plain text from a string or a rich-text document (nodes with `text` and `content`)."""
    if isinstance(value, str):
        return value.strip()
    parts: list[str] = []
    _collect(value, parts, 0)
    return "\n".join(p for p in parts if p).strip()


def _collect(value: Any, parts: list[str], depth: int) -> None:
    if depth > 30:
        return
    if isinstance(value, Mapping):
        if isinstance(value.get("text"), str):
            parts.append(value["text"])
        content = value.get("content")
        if isinstance(content, list):
            inline = [c for c in content if isinstance(c, Mapping) and c.get("type") == "text"]
            if inline and len(inline) == len(content):
                parts.append("".join(s(c.get("text")) for c in inline))
                return
            for c in content:
                _collect(c, parts, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _collect(v, parts, depth + 1)


def mentioned(arguments: Mapping[str, Any]) -> frozenset[str]:
    """Every scalar argument value as text: which pull request or path a tool call was about."""
    found: set[str] = set()
    for v in arguments.values():
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            found.add(str(v))
        elif isinstance(v, list):
            found.update(str(x) for x in v if isinstance(x, (str, int)))
    return frozenset(found)


def verbatim(value: str, seen: str) -> bool:
    return bool(value) and value in seen
