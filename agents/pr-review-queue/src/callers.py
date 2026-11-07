"""Pure: pick a discovered tool for one read (or the nudge write) by the words in its name, and fill its
arguments from its own input schema with checked values (owner, repository, number, commit).

Code calls the per-PR reads itself, so no model has to copy ids across a hundred calls. A tool whose
required arguments cannot all be filled is not used. No provider or tool is named here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from nasiko_mcp import McpTool
from send_guard import name_tokens

PAGE_SIZE = 100
_PULL = frozenset({"pull", "pr", "mr"})
_NOT_PR_ITSELF = frozenset({"review", "reviewer", "comment", "commit", "file", "check", "status", "event", "timeline",
                            "requested", "label", "assignee", "merge", "update", "branch", "diff"})
_READ = frozenset({"get", "list", "fetch", "retrieve", "read", "search", "find"})

OWNER_KEYS = frozenset({"owner", "repo_owner", "org", "organization", "namespace", "workspace"})
REPO_KEYS = frozenset({"repo", "repository", "repo_name", "repository_name", "repo_slug"})
FULL_KEYS = frozenset({"full_name", "repo_full_name", "repository_full_name", "project_id", "project"})
NUMBER_KEYS = frozenset({"pull_number", "pr_number", "number", "issue_number", "pull_request_number",
                         "pull_request_id", "merge_request_iid", "pullnumber", "prnumber", "issuenumber", "pr_id"})
SHA_KEYS = frozenset({"ref", "sha", "commit_sha", "head_sha", "commit", "commit_ref"})
PAGE_KEYS = frozenset({"per_page", "perpage", "page_size", "pagesize", "limit", "first"})
BODY_KEYS = frozenset({"body", "text", "comment", "content", "message", "note"})


def _tokens(tool: McpTool) -> frozenset[str]:
    out = set()
    for t in name_tokens(tool.name):
        out.add(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith(("us", "ss")) else t)
    if {"merge", "request"} <= out:
        out.add("mr")
    return frozenset(out)


def _is_pull(tokens: frozenset[str]) -> bool:
    return bool(tokens & _PULL)


def fits(tool: McpTool, purpose: str) -> bool:
    t = _tokens(tool)
    read = bool(t & _READ)
    about_pr = _is_pull(t) and not (t & (_NOT_PR_ITSELF - {"merge"}))
    rules = {
        "list_prs": read and bool(t & {"list", "search"}) and about_pr,
        "get_pr": read and "list" not in t and "search" not in t and about_pr,
        "reviews": read and "review" in t and not t & {"comment", "requested", "reviewer", "thread"},
        "comments": read and "comment" in t and not t & {"review", "commit", "gist", "discussion"},
        "review_comments": read and {"review", "comment"} <= t,
        "commits": read and "commit" in t and _is_pull(t) and not t & {"comment", "status", "check"},
        "check_runs": read and "check" in t and bool(t & {"run", "suite"}) and not t & {"annotation", "log"},
        "status": read and "status" in t and bool(t & {"combined", "commit", "ref", "reference"}),
        "nudge": "comment" in t and bool(t & {"create", "add", "post", "new"})
                 and not t & {"review", "commit", "delete", "update", "edit", "reaction", "reply", "gist"},
    }
    return bool(rules.get(purpose))


def pick(tools: Iterable[McpTool], purpose: str, context: Mapping[str, Any]) -> tuple[McpTool, dict] | None:
    """The first tool for `purpose` whose required arguments can all be filled from `context`."""
    for tool in tools:
        if fits(tool, purpose):
            args = fill(tool, context)
            if args is not None:
                return tool, args
    return None


def fill(tool: McpTool, context: Mapping[str, Any]) -> dict | None:
    schema = tool.input_schema or {}
    props = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    required = [r for r in schema.get("required") or () if isinstance(r, str)]
    args: dict[str, Any] = {}
    for name, spec in props.items():
        value = _value(name.casefold(), spec if isinstance(spec, Mapping) else {}, context)
        if value is not None:
            args[name] = value
    if any(r not in args for r in required):
        return None
    names_repo = any(k.casefold() in REPO_KEYS | FULL_KEYS for k in args)
    return args if names_repo else None  # a call that names no repository can't be the one we mean


def _value(key: str, spec: Mapping[str, Any], c: Mapping[str, Any]) -> Any:
    owner, repo = c.get("owner"), c.get("repo")
    if key in OWNER_KEYS:
        return owner
    if key in REPO_KEYS:
        return repo
    if key in FULL_KEYS and owner and repo:
        return f"{owner}/{repo}"
    if key in NUMBER_KEYS and c.get("number"):
        return _typed(str(c["number"]), spec)
    if key in SHA_KEYS and c.get("sha"):
        return c["sha"]
    if key in BODY_KEYS and c.get("body"):
        return c["body"]
    if key == "state" and c.get("list"):
        enum = spec.get("enum")
        return "open" if not enum or "open" in enum else None
    if key in PAGE_KEYS and c.get("list"):
        maximum = spec.get("maximum")
        return min(PAGE_SIZE, int(maximum)) if isinstance(maximum, (int, float)) else PAGE_SIZE
    return None


def _typed(number: str, spec: Mapping[str, Any]) -> Any:
    kind = spec.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    return int(number) if ("integer" in kinds or "number" in kinds) and number.isdigit() else number


def unique(tools: Sequence[McpTool]) -> tuple[McpTool, ...]:
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
