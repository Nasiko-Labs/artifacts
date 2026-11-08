"""Pure: read the facts out of raw code-host tool output by shape (never from the model's prose).

- code-search hits: objects with a `path` and a repository (`repository.full_name`, `repository` or a
  `.../<owner>/<repo>/blob/...` link), with any text fragments they carry
- file contents: objects with `content` (base64 decoded when marked so) and a `path`/`name`, or a plain-text
  result for the path named in the call's arguments
- commits: objects with `commit.author.{name,date}` and an optional `author.login`
Tool output is often wrapped (`{"data": ..., "successful": true}`); every walk goes through nested values.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

MAX_WALK_DEPTH = 8
_REPO = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
_BLOB_LINK = re.compile(r"^https?://[^/]+/([^/]+/[^/]+)/(?:blob|tree|raw)/")
_REPO_QUALIFIER = re.compile(r"\brepo:([A-Za-z0-9][\w.-]*/[\w.-]+)")
_QUOTES = re.compile(r"[\"'`\\]")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Hit:
    repo: str
    path: str
    fragments: tuple[str, ...] = ()
    query: str = ""          # the call's arguments, flattened (what was searched)
    line: int = 0            # known only for pasted grep output


@dataclass(frozen=True)
class FileText:
    repo: str
    path: str
    text: str


@dataclass(frozen=True)
class Commit:
    author: str
    date: str                # ISO 8601 as the tool gave it
    path: str = ""           # the path the call asked about


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def norm(text: str) -> str:
    """For containment checks: quotes and backslashes dropped, whitespace collapsed."""
    return _SPACES.sub(" ", _QUOTES.sub(" ", text or "")).strip()


def contains(haystack: str, needle: str) -> bool:
    small = norm(needle)
    return bool(small) and small in norm(haystack)


def flatten(arguments: Mapping[str, Any]) -> str:
    """Every string value in the call's arguments, joined: what the call searched for."""
    out: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, Mapping):
            for inner in value.values():
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(arguments)
    return " ".join(out)


def repo_of(arguments: Mapping[str, Any]) -> str:
    owner, repo = arguments.get("owner"), arguments.get("repo")
    if isinstance(owner, str) and isinstance(repo, str) and owner and repo and "/" not in repo:
        return f"{owner}/{repo}"
    for key in ("repo", "repository", "full_name", "repo_name", "repository_name"):
        value = arguments.get(key)
        if isinstance(value, str) and _REPO.match(value.strip()):
            return value.strip()
    found = _REPO_QUALIFIER.search(flatten(arguments))
    return found.group(1) if found else ""


def path_of(arguments: Mapping[str, Any]) -> str:
    for key in ("path", "file_path", "filepath", "file"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("/")
    return ""


def _walk(value: Any, depth: int = 0) -> Iterator[Mapping[str, Any]]:
    if depth > MAX_WALK_DEPTH:
        return
    if isinstance(value, Mapping):
        yield value
        for inner in value.values():
            yield from _walk(inner, depth + 1)
    elif isinstance(value, list):
        for inner in value:
            yield from _walk(inner, depth + 1)


def _hit_repo(item: Mapping[str, Any]) -> str:
    repository = item.get("repository")
    if isinstance(repository, Mapping):
        name = repository.get("full_name") or repository.get("nameWithOwner")
        if isinstance(name, str) and _REPO.match(name):
            return name
    if isinstance(repository, str) and _REPO.match(repository):
        return repository
    for key in ("html_url", "url", "web_url"):
        link = item.get(key)
        found = _BLOB_LINK.match(link) if isinstance(link, str) else None
        if found:
            return found.group(1)
    return ""


def _fragments(item: Mapping[str, Any]) -> tuple[str, ...]:
    out = [m.get("fragment") for m in item.get("text_matches") or () if isinstance(m, Mapping)]
    out += [item.get(k) for k in ("fragment", "snippet", "preview")]
    return tuple(f for f in out if isinstance(f, str) and f)


def code_hits(text: str, arguments: Mapping[str, Any]) -> list[Hit]:
    data = parse_json(text)
    query, asked_repo = flatten(arguments), repo_of(arguments)
    hits = []
    for item in _walk(data):
        path = item.get("path")
        if not isinstance(path, str) or not path or "content" in item:
            continue
        repo = _hit_repo(item) or asked_repo
        if repo:
            hits.append(Hit(repo, path.lstrip("/"), _fragments(item), query))
    return hits


def file_texts(text: str, arguments: Mapping[str, Any]) -> list[FileText]:
    data = parse_json(text)
    asked_repo, asked_path = repo_of(arguments), path_of(arguments)
    if data is None:
        return [FileText(asked_repo, asked_path, text)] if asked_path and text.strip() else []
    out = []
    for item in _walk(data):
        content = item.get("content")
        if not isinstance(content, str):
            continue
        path = item.get("path") or item.get("name") or asked_path
        decoded = _decode(content, str(item.get("encoding") or ""))
        if isinstance(path, str) and path and decoded is not None:
            out.append(FileText(_hit_repo(item) or asked_repo, path.lstrip("/"), decoded))
    return out


def _decode(content: str, encoding: str) -> str | None:
    if encoding.casefold() != "base64":
        return content
    try:
        return base64.b64decode(content, validate=False).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return None


def commits(text: str, arguments: Mapping[str, Any]) -> list[Commit]:
    data = parse_json(text)
    path = path_of(arguments)
    out = []
    for item in _walk(data):
        inner = item.get("commit")
        if not isinstance(inner, Mapping) or not isinstance(inner.get("author"), Mapping):
            continue
        author = inner["author"]
        account = item.get("author") if isinstance(item.get("author"), Mapping) else {}
        who = account.get("login") or author.get("name") or author.get("email")
        date = author.get("date") or ""
        if isinstance(who, str) and who and isinstance(date, str):
            out.append(Commit(who, date, path))
    return out
