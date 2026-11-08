"""Pure: repository records out of a code-host tool's JSON output, whatever its envelope. A record is a dict
with an owner/name and a star count; numbers are read as given, never estimated. Also: filling a tool's
arguments from its input schema (only well-known parameter names; a tool needing anything else is skipped)."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator, Mapping
from typing import Any

from store import Item

README_CHARS = 1_500
DESCRIPTION_CHARS = 400
SEARCH_PAGE = 10
_QUERY_KEYS = ("query", "q", "search", "search_query", "keyword", "keywords", "term")
_PAGE_KEYS = ("per_page", "limit", "max_results", "page_size", "first")
_REPO_KEYS = ("full_name", "repo_full_name", "repository", "repo", "repo_name")
_STAR_KEYS = ("stargazers_count", "stars", "star_count", "stargazerCount")
_FORK_KEYS = ("forks_count", "forks", "fork_count", "forkCount")
_PUSH_KEYS = ("pushed_at", "pushedAt", "last_activity_at", "updated_at", "updatedAt")
_URL_KEYS = ("html_url", "web_url", "url")
_MAX_DEPTH = 6


def properties(schema: Mapping[str, Any]) -> tuple[Mapping[str, Any], set[str]]:
    props = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    required = set(schema.get("required") or ())
    return props, required


def search_args(schema: Mapping[str, Any], topic: str) -> dict | None:
    props, required = properties(schema)
    key = next((k for k in _QUERY_KEYS if k in props), None)
    if key is None:
        return None
    args: dict[str, Any] = {key: topic}
    page = next((k for k in _PAGE_KEYS if k in props), None)
    if page:
        args[page] = SEARCH_PAGE
    sort = props.get("sort") if isinstance(props.get("sort"), Mapping) else {}
    if "stars" in (sort.get("enum") or ()):
        args["sort"] = "stars"
    return args if required <= set(args) else None


def repo_args(schema: Mapping[str, Any], full_name: str) -> dict | None:
    props, required = properties(schema)
    owner, _, name = full_name.partition("/")
    if "owner" in props and ("repo" in props or "name" in props):
        args = {"owner": owner, ("repo" if "repo" in props else "name"): name}
    else:
        key = next((k for k in _REPO_KEYS if k in props), None)
        if key is None:
            return None
        args = {key: full_name}
    return args if required <= set(args) else None


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def records(payload: Any) -> list[Item]:
    seen: dict[str, Item] = {}
    for record in _walk(payload, 0):
        item = _item(record)
        if item is not None:
            seen.setdefault(item.title.casefold(), item)
    return list(seen.values())


def _walk(node: Any, depth: int) -> Iterator[Mapping[str, Any]]:
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, Mapping):
        yield node
        for value in node.values():
            yield from _walk(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, depth + 1)


def _first(record: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    return next((record[k] for k in keys if record.get(k) is not None), None)


def _item(record: Mapping[str, Any]) -> Item | None:
    full = record.get("full_name") or record.get("nameWithOwner")
    owner = record.get("owner")
    if not full and isinstance(owner, Mapping) and owner.get("login") and record.get("name"):
        full = f"{owner['login']}/{record['name']}"
    stars = _first(record, _STAR_KEYS)
    if not isinstance(full, str) or "/" not in full or not isinstance(stars, int):
        return None
    url = next((str(record[k]) for k in _URL_KEYS if str(record.get(k) or "").startswith("https://")
                and "://api." not in str(record[k])), "")
    topics = [str(t) for t in record.get("topics") or [] if isinstance(t, str)]
    description = str(record.get("description") or "")[:DESCRIPTION_CHARS]
    text = "\n".join(t for t in (full, description, ("topics: " + ", ".join(topics)) if topics else "") if t)
    forks = _first(record, _FORK_KEYS)
    return Item(source="code", title=full, url=url, text=text, date=str(_first(record, _PUSH_KEYS) or ""),
                stars=max(0, stars), forks=max(0, forks) if isinstance(forks, int) else 0)


def readme_text(payload: Any, raw_text: str) -> str:
    """A README's first 1,500 chars from a JSON body (base64 or plain `content`) or plain text."""
    if isinstance(payload, Mapping):
        for record in _walk(payload, 0):
            content = record.get("content") or record.get("text") or record.get("readme")
            if not isinstance(content, str) or not content.strip():
                continue
            if str(record.get("encoding") or "").casefold() == "base64":
                try:
                    content = base64.b64decode(content).decode("utf-8", "replace")
                except (binascii.Error, ValueError):
                    continue
            return content[:README_CHARS]
        return ""
    return (raw_text or "")[:README_CHARS]
