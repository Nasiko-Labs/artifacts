"""Pure: a write step's logical values placed on the write tool's own argument names, and the same values
read back out of a call's arguments (the send guard compares those with the plan). Both directions use the
same name tables, so what the guard reads is exactly what a call would carry. File content is base64-encoded
only when the tool's own schema says so."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import McpTool
from writeplan import Step

OWNER = ("owner", "repo_owner", "org", "organization", "namespace", "workspace")
REPO = ("repo", "repo_name", "repository", "repository_name")
FULL = ("repo_full_name", "full_name", "repository_full_name", "project_id", "project", "repo_slug", "slug")
NEW_BRANCH = ("branch", "branch_name", "new_branch", "new_branch_name", "name")
FROM_BRANCH = ("from_branch", "base_branch", "source_branch", "from", "source", "start_point", "from_ref")
COMMIT_BRANCH = ("branch", "branch_name", "target_branch")
PATH = ("path", "file_path", "filepath")
CONTENT = ("content", "contents", "file_content", "text")
MESSAGE = ("message", "commit_message")
BLOB_SHA = ("sha", "blob_sha", "file_sha", "last_commit_id")
TITLE = ("title",)
BODY = ("body", "description")
HEAD = ("head", "head_branch", "source_branch", "from_branch")
BASE = ("base", "base_branch", "target_branch", "into")
DRAFT = ("draft", "is_draft")
REFS_HEADS = "refs/heads/"


def _props(tool: McpTool) -> dict[str, str]:
    return {str(k).casefold(): str(k) for k in (tool.input_schema.get("properties") or {})}


def _key(props: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    return next((props[n] for n in names if n in props), None)


def wants_base64(tool: McpTool) -> bool:
    props = tool.input_schema.get("properties") or {}
    key = _key(_props(tool), CONTENT)
    spec = props.get(key) if key else None
    return isinstance(spec, Mapping) and "base64" in str(spec.get("description") or "").casefold()


def needs_base_sha(tool: McpTool) -> bool:
    props = _props(tool)
    return "sha" in props and _key(props, NEW_BRANCH) is None


def _repo_args(props: Mapping[str, str], repo: str) -> dict[str, Any]:
    owner, name = repo.split("/", 1)
    out: dict[str, Any] = {}
    owner_key = _key(props, OWNER)
    if owner_key:
        out[owner_key] = owner
        repo_key = _key(props, REPO)
        if repo_key:
            out[repo_key] = name
    else:
        full_key = _key(props, FULL) or _key(props, REPO)
        if full_key:
            out[full_key] = repo
    return out


def build(tool: McpTool, step: Step, content: str = "", base_sha: str = "") -> dict[str, Any]:
    props = _props(tool)
    args = _repo_args(props, step.repo)

    def put(names: tuple[str, ...], value: Any) -> None:
        key = _key(props, names)
        if key is not None and key not in args and value not in (None, ""):
            args[key] = value

    if step.kind == "branch":
        if _key(props, NEW_BRANCH):
            put(NEW_BRANCH, step.branch)
            put(FROM_BRANCH, step.base)
            put(("ref",), step.base)
            put(("sha",), base_sha)
        else:
            put(("ref",), REFS_HEADS + step.branch)
            put(("sha",), base_sha)
    elif step.kind == "commit":
        put(PATH, step.path)
        put(COMMIT_BRANCH, step.branch)
        put(MESSAGE, step.message)
        encoded = base64.b64encode(content.encode("utf-8")).decode() if wants_base64(tool) else content
        key = _key(props, CONTENT)
        if key is not None:
            args[key] = encoded
        put(BLOB_SHA, step.blob_sha)
    elif step.kind == "pr":
        put(TITLE, step.title)
        put(BODY, step.body)
        put(HEAD, step.branch)
        put(BASE, step.base)
        key = _key(props, DRAFT)
        if key is not None:
            args[key] = True
    return args


def read(tool: McpTool, kind: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """The logical values a call's arguments carry (missing ones are None)."""
    lowered = {str(k).casefold(): v for k, v in args.items()}

    def get(names: tuple[str, ...]) -> Any:
        return next((lowered[n] for n in names if n in lowered), None)

    owner, name, full = get(OWNER), get(REPO), get(FULL)
    repo = f"{owner}/{name}" if owner and name else (full or name)
    out: dict[str, Any] = {"repo": repo}
    if kind == "branch":
        branch = get(NEW_BRANCH)
        ref = get(("ref",))
        if branch is None and isinstance(ref, str):
            branch = ref.removeprefix(REFS_HEADS)
        out["branch"] = branch
    elif kind == "commit":
        out.update(path=get(PATH), branch=get(COMMIT_BRANCH), message=get(MESSAGE))
        content = get(CONTENT)
        out["content"] = _decoded(content) if wants_base64(tool) else content
    elif kind == "pr":
        out.update(title=get(TITLE), body=get(BODY), head=get(HEAD), base=get(BASE), draft=get(DRAFT))
    return out


def _decoded(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None
