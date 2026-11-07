"""Pure: read raw tool output by its shape, never by tool name. Every value comes from a recorded call
(`fence.Call`), so whatever reaches the guide was returned by a tool.

Shapes: a repository (full_name + description/default_branch), directory entries (path + type),
file content (content, base64 or plain), code search hits (path in a search result), pull requests
(number + title, merged), an issue (key/identifier/number + title), a document (title + link)."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fence import Call

MAX_FILE_CHARS = 24_000
TRUNCATED_MARK = "[truncated]"
_MAX_DEPTH = 6
_PATH_ARGS = ("path", "file_path", "filepath", "file", "filename")
_LINK_KEYS = ("html_url", "web_url", "webViewLink", "url", "link", "permalink")
_DIR_TYPES = {"dir": "dir", "tree": "dir", "file": "file", "blob": "file"}


@dataclass(frozen=True)
class RepoInfo:
    full_name: str = ""
    description: str = ""
    default_branch: str = ""
    link: str = ""


@dataclass(frozen=True)
class FileText:
    path: str
    text: str
    truncated: bool = False
    link: str = ""


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    author: str = ""
    merged_at: str = ""
    link: str = ""
    body: str = ""
    files: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Issue:
    key: str
    title: str
    body: str = ""
    link: str = ""


@dataclass(frozen=True)
class Doc:
    title: str
    link: str


def parse(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def walk(value: Any, depth: int = 0) -> Iterator[Mapping[str, Any]]:
    """Every mapping inside a parsed result (wrappers such as {"data": ...} included), outermost first."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, Mapping):
        yield value
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return
    for child in children:
        if isinstance(child, (Mapping, list)):
            yield from walk(child, depth + 1)


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _link(d: Mapping[str, Any]) -> str:
    for key in _LINK_KEYS:
        value = _str(d.get(key))
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _login(value: Any) -> str:
    if isinstance(value, Mapping):
        return _str(value.get("login")) or _str(value.get("username")) or _str(value.get("name"))
    return _str(value)


# --- repository and layout ---------------------------------------------------------------------------

def repo_info(calls: Sequence[Call]) -> RepoInfo | None:
    for call in calls:
        for d in walk(parse(call.text)):
            name = _str(d.get("full_name"))
            if name and ("description" in d or "default_branch" in d):
                return RepoInfo(name, _str(d.get("description")), _str(d.get("default_branch")), _link(d))
    return None


def entries(calls: Sequence[Call]) -> dict[str, str]:
    """path -> "dir" | "file", from directory listings and trees."""
    found: dict[str, str] = {}
    for call in calls:
        for d in walk(parse(call.text)):
            kind = _DIR_TYPES.get(_str(d.get("type")))
            path = _str(d.get("path"))
            if kind and path and "content" not in d:
                found = {**found, path.strip("/"): kind}
    return found


# --- file content ------------------------------------------------------------------------------------

def files(calls: Sequence[Call]) -> dict[str, FileText]:
    """path -> the file's text (base64 decoded; cut at MAX_FILE_CHARS with a visible mark)."""
    found: dict[str, FileText] = {}
    for call in calls:
        item = _file_of(call)
        if item is not None and item.path not in found:
            found = {**found, item.path: item}
    return found


def _file_of(call: Call) -> FileText | None:
    arg_path = next((_str(call.arguments.get(k)) for k in _PATH_ARGS if _str(call.arguments.get(k))), "")
    parsed = parse(call.text)
    for d in walk(parsed):
        content = d.get("content")
        if not isinstance(content, str) or _str(d.get("type")) in ("dir", "tree"):
            continue
        path = _str(d.get("path")) or arg_path
        text = _decode(content) if _str(d.get("encoding")).casefold() == "base64" else content
        if path and text is not None:
            return _cut(path.strip("/"), text, _link(d))
    if parsed is None and arg_path and call.text.strip():
        return _cut(arg_path.strip("/"), call.text, "")  # a raw file body
    return None


def _decode(content: str) -> str | None:
    try:
        return base64.b64decode("".join(content.split()), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _cut(path: str, text: str, link: str) -> FileText:
    if len(text) <= MAX_FILE_CHARS:
        return FileText(path, text, False, link)
    return FileText(path, text[:MAX_FILE_CHARS] + "\n" + TRUNCATED_MARK, True, link)


# --- code search ---------------------------------------------------------------------------------------

def search_hits(call: Call, repo: str) -> tuple[str, ...]:
    """Paths a code search returned, limited to `repo` when a hit names its repository."""
    paths: list[str] = []
    for d in walk(parse(call.text)):
        path = _str(d.get("path"))
        if not path or _DIR_TYPES.get(_str(d.get("type"))) == "dir":
            continue
        owner = d.get("repository")
        named = _str(owner.get("full_name")) if isinstance(owner, Mapping) else _str(owner)
        if named and repo and named.casefold() != repo.casefold():
            continue
        clean = path.strip("/")
        if clean not in paths:
            paths.append(clean)
    return tuple(paths)


# --- pull requests -----------------------------------------------------------------------------------

def pull_requests(calls: Sequence[Call]) -> tuple[PullRequest, ...]:
    """Merged pull requests, by number (first seen wins)."""
    found: dict[int, PullRequest] = {}
    for call in calls:
        for d in walk(parse(call.text)):
            pr = _pr_of(d)
            if pr is not None and pr.number not in found:
                found = {**found, pr.number: pr}
    return tuple(found.values())


def _pr_of(d: Mapping[str, Any]) -> PullRequest | None:
    number, title = d.get("number"), _str(d.get("title"))
    if not isinstance(number, int) or isinstance(number, bool) or not title:
        return None
    nested = d.get("pull_request")
    is_pr = isinstance(nested, Mapping) or "merged_at" in d or "/pull/" in _link(d)
    if not is_pr:
        return None
    merged = _str(d.get("merged_at")) or (_str(nested.get("merged_at")) if isinstance(nested, Mapping) else "")
    if not merged:
        return None
    raw_files = d.get("files")
    paths = tuple(_str(f.get("filename") or f.get("path")) if isinstance(f, Mapping) else _str(f)
                  for f in raw_files) if isinstance(raw_files, list) else ()
    return PullRequest(number, title, _login(d.get("user") or d.get("author")), merged, _link(d),
                       _str(d.get("body")), tuple(p for p in paths if p))


# --- tickets and documents -----------------------------------------------------------------------------

def issue(calls: Sequence[Call], wanted: str) -> Issue | None:
    """The ticket asked for (by key, #number or URL); else the only issue-shaped item returned."""
    candidates: list[Issue] = []
    for call in calls:
        for d in walk(parse(call.text)):
            item = _issue_of(d)
            if item is not None:
                candidates.append(item)
    want = wanted.strip().lstrip("#").casefold()
    for item in candidates:
        if item.key.lstrip("#").casefold() == want or (item.link and item.link.casefold() == want):
            return item
    return candidates[0] if len(candidates) == 1 else None


def _issue_of(d: Mapping[str, Any]) -> Issue | None:
    fields = d.get("fields") if isinstance(d.get("fields"), Mapping) else {}
    title = _str(d.get("title")) or _str(d.get("summary")) or _str(fields.get("summary"))
    key = _str(d.get("identifier")) or _str(d.get("key"))
    if not key and isinstance(d.get("number"), int) and not isinstance(d.get("number"), bool):
        key = f"#{d['number']}"
    if not key or not title:
        return None
    body = _str(d.get("description")) or _str(d.get("body")) or _str(fields.get("description"))
    return Issue(key, title, body, _link(d))


def documents(calls: Sequence[Call]) -> tuple[Doc, ...]:
    found: dict[str, Doc] = {}
    for call in calls:
        for d in walk(parse(call.text)):
            title = _str(d.get("title")) or _str(d.get("name"))
            link = _link(d)
            if title and link and link not in found:
                found = {**found, link: Doc(title, link)}
    return tuple(found.values())
