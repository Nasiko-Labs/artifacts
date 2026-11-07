"""Pure: read repositories, branches, directory listings, files, dependency alerts and write results out
of raw tool output by their shape (the field names a code-host API returns), so code reads the facts
itself instead of trusting the model to copy them. Every value here came out of a tool result this turn.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

_OWNER_KEYS = ("owner", "repo_owner", "org", "organization", "namespace")
_REPO_KEYS = ("repo", "repository", "repo_name", "name", "repository_name")


@dataclass(frozen=True)
class FileContent:
    path: str
    sha: str
    text: str
    url: str = ""


@dataclass(frozen=True)
class Repo:
    full_name: str
    default_branch: str = ""
    archived: bool = False


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


def repos(text: str) -> list[Repo]:
    """Repository objects (`full_name` with an owner or a default branch)."""
    out: dict[str, Repo] = {}
    for o in objects(text):
        name = s(o.get("full_name"))
        if "/" in name and ("default_branch" in o or "owner" in o or "private" in o):
            out.setdefault(name.casefold(), Repo(name, s(o.get("default_branch")), bool(o.get("archived"))))
    return list(out.values())


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
            out.append(FileContent(path, sha, body, s(o.get("html_url"))))
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


def listed_paths(text: str) -> list[str]:
    """Entries of a directory listing (`{"type": "file"|"dir", "path"}` without a body)."""
    out = [s(o["path"]) for o in objects(text)
           if s(o.get("path")) and s(o.get("type")) in ("file", "dir") and "content" not in o]
    return list(dict.fromkeys(out))


def alert_packages(text: str) -> set[str]:
    """Package names a dependency-alert listing names (`dependency.package.name` or `package.name`)."""
    found = set()
    for o in objects(text):
        dependency = o.get("dependency")
        package = dependency.get("package") if isinstance(dependency, Mapping) else None
        if isinstance(package, Mapping) and s(package.get("name")) and ("state" in o or "security_advisory" in o):
            found.add(s(package["name"]).casefold())
    return found


def repo_of(arguments: Mapping[str, Any]) -> str:
    """`owner/repo` a tool call was about, from its arguments ("" when it does not say)."""
    lowered = {str(k).casefold(): v for k, v in arguments.items()}
    for key in ("full_name", "repository", "repo"):
        value = s(lowered.get(key))
        if "/" in value:
            return value.strip("/")
    owner = next((s(lowered[k]) for k in _OWNER_KEYS if s(lowered.get(k))), "")
    repo = next((s(lowered[k]) for k in _REPO_KEYS if s(lowered.get(k))), "")
    return f"{owner}/{repo}" if owner and repo else ""


def created_number(text: str) -> tuple[str, str]:
    """(number, link) of a pull request a write just created."""
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
