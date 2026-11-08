"""Pure: facts read out of raw code-host tool output, in code: file contents (by path) and the commits
that changed a path. Shapes vary by connector, so dicts are searched recursively for the fields that
carry them; anything not found stays unknown. Every value here appears verbatim in a tool result."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

_PATH_ARGS = ("path", "file_path", "filepath", "file", "path_filter")
_CONTENT_KEYS = ("content", "decoded_content", "text", "contents")


@dataclass(frozen=True)
class FileText:
    path: str
    text: str


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str  # `@login` when the code host gives one, else the commit author's name
    date: str    # YYYY-MM-DD as returned
    message: str = ""


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def path_argument(arguments: Mapping[str, Any]) -> str:
    return next((str(arguments[k]) for k in _PATH_ARGS if isinstance(arguments.get(k), str) and arguments[k]), "")


def files(text: str, arguments: Mapping[str, Any]) -> tuple[FileText, ...]:
    """Files in one tool result. A plain-text result is the file the call asked for."""
    data = parse_json(text)
    if data is None:
        asked = path_argument(arguments)
        return (FileText(asked, text),) if asked and text.strip() else ()
    out = []
    for node in _dicts(data):
        path = node.get("path") if isinstance(node.get("path"), str) else ""
        content = _content(node)
        if content is not None and (path or path_argument(arguments)):
            out.append(FileText(path or path_argument(arguments), content))
    return tuple(out)


def _content(node: Mapping[str, Any]) -> str | None:
    for key in _CONTENT_KEYS:
        value = node.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if key == "content" and str(node.get("encoding") or "").casefold() == "base64":
            return _b64(value)
        return value
    return None


def _b64(value: str) -> str | None:
    try:
        return base64.b64decode("".join(value.split()), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def commits(text: str) -> tuple[Commit, ...]:
    data = parse_json(text)
    if data is None:
        return ()
    out = []
    for node in _dicts(data):
        sha = node.get("sha")
        detail = node.get("commit")
        if not isinstance(sha, str) or not isinstance(detail, Mapping):
            continue
        author_info = detail.get("author") if isinstance(detail.get("author"), Mapping) else {}
        login = node.get("author", {}).get("login") if isinstance(node.get("author"), Mapping) else None
        name = login and f"@{login}" or author_info.get("name") or ""
        date = str(author_info.get("date") or "")[:10]
        if name and date:
            out.append(Commit(sha, str(name), date, str(detail.get("message") or "").split("\n")[0]))
    return tuple(out)


def newest(found: tuple[Commit, ...]) -> Commit | None:
    return max(found, key=lambda c: c.date, default=None)


def _dicts(node: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(node, Mapping):
        yield node
        for value in node.values():
            yield from _dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _dicts(value)
