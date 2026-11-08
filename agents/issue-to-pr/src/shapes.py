"""Pure: facts read by shape out of raw tool output (issue, comments, default branch, directory entries, search
hits, file text), whatever the code host's exact field layout. Nothing here trusts the model."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import ToolOk

MAX_WALK_NODES = 20_000
TRUNCATION_MARKERS = ("[truncated]", "[... truncated ...]", "…[truncated]", "[output truncated]")
_DIR_TYPES = frozenset({"dir", "tree", "directory", "folder"})
_FILE_TYPES = frozenset({"file", "blob"})


@dataclass(frozen=True)
class IssueDoc:
    title: str
    body: str
    labels: tuple[str, ...] = ()
    comments: tuple[str, ...] = ()
    number: int | None = None

    def text(self) -> str:
        parts = [self.title, self.body, *self.comments]
        return "\n".join(p for p in parts if p)


def result_text(result: ToolOk) -> str:
    """Text items plus embedded resource text, joined."""
    texts = []
    for item in result.content:
        if item.get("type") == "text":
            texts.append(str(item.get("text", "")))
        elif item.get("type") == "resource" and isinstance(item.get("resource"), Mapping):
            texts.append(str(item["resource"].get("text") or ""))
    return "\n".join(texts)


def payload(result: ToolOk) -> Any:
    """Structured content, else the text as JSON, else the text itself; wrapper envelopes are unwrapped."""
    structured = result.raw.get("structuredContent") if isinstance(result.raw, Mapping) else None
    data = structured if structured is not None else _json(result_text(result))
    if data is None:
        return result_text(result)
    for _ in range(3):
        if isinstance(data, Mapping) and isinstance(data.get("data"), (Mapping, list)) and \
                ({"successful", "success", "error", "ok"} & set(data) or len(data) <= 2):
            data = data["data"]
        else:
            break
    return data


def issue_from(data: Any) -> IssueDoc | None:
    node = next((d for d in _dicts(data) if "title" in d and ("body" in d or "description" in d)), None)
    if node is None:
        return None
    labels = node.get("labels") if isinstance(node.get("labels"), list) else []
    names = tuple(str(lb.get("name") if isinstance(lb, Mapping) else lb) for lb in labels)
    number = node.get("number") or node.get("iid")
    return IssueDoc(str(node.get("title") or ""), str(node.get("body") or node.get("description") or ""),
                    names, (), int(number) if isinstance(number, int) else None)


def issue_closed(data: Any) -> bool:
    node = next((d for d in _dicts(data) if "title" in d), None)
    return node is not None and str(node.get("state") or "").casefold() in ("closed", "close")


def comments_from(data: Any, limit: int = 10) -> tuple[str, ...]:
    bodies = [str(d["body"]) for d in _dicts(data)
              if isinstance(d.get("body"), str) and ({"user", "author", "created_at", "id"} & set(d)) and "title" not in d]
    return tuple(bodies[-limit:])


def default_branch(data: Any) -> str | None:
    for d in _dicts(data):
        value = d.get("default_branch") or d.get("defaultBranch")
        if isinstance(value, str) and value:
            return value
    return None


def entries(data: Any, base: str) -> tuple[list[str], list[str]]:
    """(files, dirs) from a directory listing or a tree, as repository paths."""
    files, dirs = [], []
    for d in _dicts(data):
        kind = str(d.get("type") or "").casefold()
        raw = d.get("path") or d.get("name")
        if kind not in _DIR_TYPES | _FILE_TYPES or not isinstance(raw, str) or not raw:
            continue
        path = raw.strip("/") if "/" in raw or not base else f"{base.strip('/')}/{raw}".strip("/")
        (dirs if kind in _DIR_TYPES else files).append(path)
    return list(dict.fromkeys(files)), list(dict.fromkeys(dirs))


def search_paths(data: Any) -> list[str]:
    hits = [d["path"] for d in _dicts(data) if isinstance(d.get("path"), str)
            and ({"repository", "sha", "html_url", "url", "name", "text_matches"} & set(d))
            and str(d.get("type") or "file").casefold() not in _DIR_TYPES]
    return [h.strip("/") for h in hits]


def file_text(data: Any) -> str | None:
    if isinstance(data, str):
        return data
    for d in _dicts(data):
        content = d.get("content")
        if not isinstance(content, str):
            continue
        if str(d.get("encoding") or "").casefold() == "base64":
            try:
                return base64.b64decode(content).decode("utf-8", errors="replace")
            except (binascii.Error, ValueError):
                return None
        return content
    for d in _dicts(data):
        if isinstance(d.get("text"), str):
            return d["text"]
    return None


def file_meta(data: Any) -> tuple[str | None, str, bool]:
    """(text, blob sha, complete). Complete is False when the payload declares a size the text does not
    match (the gateway or the tool cut it): such a file is never handed to the engine."""
    text = file_text(data)
    node = next((d for d in _dicts(data) if isinstance(d.get("content"), str) or isinstance(d.get("text"), str)), None)
    sha = str(node.get("sha") or "") if node is not None else ""
    size = node.get("size") if node is not None else None
    complete = text is not None and (not isinstance(size, int) or len(text.encode("utf-8")) == size) \
        and not text.rstrip().endswith(TRUNCATION_MARKERS)
    return text, sha, complete


def commit_sha(data: Any) -> str | None:
    """The latest commit sha of a branch read (`commit.sha` or `object.sha`)."""
    for d in _dicts(data):
        for key in ("commit", "object"):
            inner = d.get(key)
            if isinstance(inner, Mapping) and isinstance(inner.get("sha"), str) and inner["sha"]:
                return inner["sha"]
    return None


_PR_URL = re.compile(r"https?://[^\s\"'<>]+/(?:pull|pulls|merge_requests)/\d+")


def pr_url(result: ToolOk) -> str | None:
    """The pull request URL, read verbatim from the tool output."""
    found = _PR_URL.search(json.dumps(result.raw.get("structuredContent")) if result.raw.get("structuredContent")
                           else result_text(result))
    return found.group(0) if found else None


def _dicts(data: Any) -> Iterator[Mapping]:
    """Every mapping in the payload, breadth first, bounded."""
    queue, seen = [data], 0
    while queue and seen < MAX_WALK_NODES:
        node = queue.pop(0)
        seen += 1
        if isinstance(node, Mapping):
            yield node
            queue.extend(v for v in node.values() if isinstance(v, (Mapping, list)))
        elif isinstance(node, list):
            queue.extend(v for v in node if isinstance(v, (Mapping, list)))


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None
