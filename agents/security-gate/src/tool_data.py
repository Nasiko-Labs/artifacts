"""Pure: facts read out of raw code-host tool output by their shape (the field names a code host API
returns): the pull request, its changed files with patches, and a file's contents. Code reads these
itself instead of trusting the model to copy them; every value came from a tool result this turn."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator, Mapping
from typing import Any

from records import ChangedFile, PullRequest

MAX_FILE_BYTES = 200_000


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def objects(text: str) -> Iterator[Mapping[str, Any]]:
    """Every JSON object at any depth of a tool output (JSON strings holding JSON are opened too)."""
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


def pull_request(text: str, repo: str, number: str) -> PullRequest | None:
    for o in objects(text):
        head, base = o.get("head"), o.get("base")
        if s(o.get("number")) == number and isinstance(head, Mapping) and isinstance(base, Mapping):
            return PullRequest(repo, number, s(o.get("title")), s(o.get("html_url") or o.get("url")),
                               s(head.get("sha")), s(base.get("sha")))
    return None


def changed_files(text: str) -> list[ChangedFile]:
    out = []
    for o in objects(text):
        name = o.get("filename")
        if isinstance(name, str) and ("patch" in o or "status" in o or "additions" in o):
            out.append(ChangedFile(name, s(o.get("status")) or "modified", s(o.get("patch")),
                                   _int(o.get("additions")), _int(o.get("deletions"))))
    return out


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def file_content(text: str, path: str) -> str | None:
    """The file's text from a contents result: a `content` field (base64 or plain) whose `path`/`name`
    matches, or a non-JSON body (a raw file). None when the result holds no contents."""
    data = parse_json(text)
    if data is None:
        return text if text and len(text.encode()) <= MAX_FILE_BYTES else None
    name = path.rsplit("/", 1)[-1]
    for o in objects(text):
        raw = o.get("decoded_content") if isinstance(o.get("decoded_content"), str) else o.get("content")
        if not isinstance(raw, str) or o.get("type") == "dir":
            continue
        where = s(o.get("path") or o.get("name"))
        if where and where not in (path, name):
            continue
        return _decode(raw, s(o.get("encoding")))
    return None


def _decode(raw: str, encoding: str) -> str | None:
    if encoding.casefold() == "base64":
        try:
            return base64.b64decode(raw, validate=False).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    return raw
