"""Code-enforced gate on every MCP tool that changes something (adapted from the promise-tracker's copy;
owned by this agent).

Any tool whose name reads as a write is refused unless it is THE write the user approved on its own card:
the same tool, the same repository, and every bound field equal to what the card showed. One approval
covers one write call. Bound fields per write kind:

- issue: title, body · comment: issue number, body
- branch: new branch name, base (the base commit, or the base branch name)
- commit: branch, path, file content (plain or base64), commit message, the file's blob sha when given
- pr: head branch, base branch, title, body

Any other argument holding free text (anything with a space) is refused, so data cannot ride along in a
field the card did not show. Case and whitespace are ignored for titles, bodies and messages; file content
must match exactly (a trailing newline aside). Pure: decisions only, no I/O.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify",
    "merge", "rerun", "rerequest", "dispatch", "trigger", "revert", "push", "lock", "unlock", "dismiss",
    "enable", "disable", "label", "comment", "put", "commit", "save", "keep", "copy",
})
READ_VERBS = frozenset({"get", "list", "search", "fetch", "find", "read", "download", "retrieve", "describe",
                        "compare"})

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")

OWNER_KEYS = frozenset({"owner", "repo_owner", "org", "organization", "namespace"})
REPO_KEYS = frozenset({"repo", "repository", "repo_name", "full_name", "repository_name"})
# family → argument keys (exact, lower-cased), per write kind.
FAMILIES: dict[str, dict[str, frozenset[str]]] = {
    "issue": {"title": frozenset({"title"}), "body": frozenset({"body", "text", "description"})},
    "comment": {"number": frozenset({"issue_number", "number", "issue_id", "pull_number"}),
                "body": frozenset({"body", "text", "comment"})},
    "branch": {"ref": frozenset({"ref", "branch", "branch_name", "new_branch", "name"}),
               "from": frozenset({"sha", "from_sha", "commit_sha", "base_sha", "from_branch", "source_branch",
                                  "base_branch", "from", "base"})},
    "commit": {"branch": frozenset({"branch", "ref", "branch_name"}),
               "path": frozenset({"path", "file_path", "filename"}),
               "content": frozenset({"content", "file_content", "contents"}),
               "message": frozenset({"message", "commit_message"}),
               "sha": frozenset({"sha", "blob_sha", "file_sha"})},
    "pr": {"head": frozenset({"head", "head_branch", "source_branch", "from_branch"}),
           "base": frozenset({"base", "base_branch", "target_branch"}),
           "title": frozenset({"title"}), "body": frozenset({"body", "description"})},
}
REQUIRED = {"issue": ("title", "body"), "comment": ("number", "body"), "branch": ("ref", "from"),
            "commit": ("branch", "path", "content", "message"), "pr": ("head", "base", "title")}
_TEXT_FAMILIES = frozenset({"title", "body", "message"})


@dataclass(frozen=True)
class Approval:
    """One write the user approved: its kind, tool, repository and the exact bound values. `alternates`
    holds other accepted spellings of one family (the base commit may be named by its branch)."""

    kind: str
    tool: str
    owner: str
    repo: str
    values: dict[str, str]
    alternates: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "tool": self.tool, "owner": self.owner, "repo": self.repo,
                "values": dict(self.values), "alternates": {k: list(v) for k, v in self.alternates.items()}}

    @staticmethod
    def from_dict(data: Any) -> Approval | None:
        if not isinstance(data, dict) or data.get("kind") not in FAMILIES or not data.get("tool"):
            return None
        return Approval(str(data["kind"]), str(data["tool"]), str(data.get("owner") or ""),
                        str(data.get("repo") or ""), {str(k): str(v) for k, v in (data.get("values") or {}).items()},
                        {str(k): tuple(str(x) for x in v) for k, v in (data.get("alternates") or {}).items()})


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    is_write: bool
    reason: str = ""


def name_tokens(tool_name: str) -> tuple[str, ...]:
    """`c_1a2b__HOST_createIssue` → ("c", "1a2b", "host", "create", "issue")."""
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    tokens = name_tokens(tool_name)
    verbs = [t for t in tokens if t in WRITE_VERBS or t in READ_VERBS]
    if verbs and verbs[0] in READ_VERBS and not any(t in WRITE_VERBS - {"comment", "label", "commit"}
                                                    for t in tokens):
        return False  # `get_issue_comment`, `list_commits`: a read about comments or commits
    rerun = any(a == "re" and b == "run" for a, b in pairwise(tokens))
    return rerun or any(token in WRITE_VERBS for token in tokens)


def check(name: str, arguments: dict, approval: Approval | None) -> GuardDecision:
    if not is_write_tool(name):
        return GuardDecision(allowed=True, is_write=False)
    if approval is None:
        return _refused(f"Refused: {name} changes the repository and needs its own approval card first. "
                        "Nothing was written.")
    if name.casefold() != approval.tool.casefold():
        return _refused(f"Refused: the approved write uses {approval.tool}, not {name}.")
    problem = _mismatch(_fields(arguments), approval)
    return _refused(problem) if problem else GuardDecision(allowed=True, is_write=True)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(allowed=False, is_write=True, reason=reason)


def _mismatch(fields: list[tuple[str, str]], approval: Approval) -> str | None:
    owner, repo = approval.owner.casefold(), approval.repo.casefold()
    repos = [v.casefold() for k, v in fields if k in REPO_KEYS]
    owners = [v.casefold() for k, v in fields if k in OWNER_KEYS]
    if not repos or any(r not in (repo, f"{owner}/{repo}") for r in repos) or any(o != owner for o in owners):
        return f"Refused: this call must write to exactly the approved repository ({approval.owner}/{approval.repo})."
    families = FAMILIES[approval.kind]
    present = set()
    for key, value in fields:
        family = next((f for f, keys in families.items() if key in keys), None)
        if family is None:
            if key not in OWNER_KEYS | REPO_KEYS and _WHITESPACE.search(value.strip()):
                return f"Refused: this call carries text the card did not show (in {key or '?'})."
            continue
        present.add(family)
        if not _matches(family, value, approval):
            return f"Refused: the {family} in this call is not the one on the approved card."
    missing = [f for f in REQUIRED[approval.kind] if f not in present]
    return f"Refused: this call leaves out the approved {', '.join(missing)}." if missing else None


def _matches(family: str, value: str, approval: Approval) -> bool:
    wanted = (approval.values.get(family, ""), *approval.alternates.get(family, ()))
    if family == "content":
        return _same_content(value, wanted[0])
    if family in _TEXT_FAMILIES:
        return _normalize(value) in {_normalize(w) for w in wanted}
    if family in ("ref", "branch", "head", "base", "from"):
        value = value.removeprefix("refs/heads/")
    if family == "number":
        value = value.lstrip("#")
    return value.strip().casefold() in {w.strip().casefold() for w in wanted if w}


def _same_content(value: str, wanted: str) -> bool:
    if value.rstrip("\n") == wanted.rstrip("\n"):
        return True
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    return decoded.rstrip("\n") == wanted.rstrip("\n")


def _fields(value: object, key: str = "") -> list[tuple[str, str]]:
    """Every scalar in the arguments with the (lower-cased) key it sits under, at any depth."""
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, int) and not isinstance(value, bool):
        return [(key, str(value))]
    if isinstance(value, dict):
        return [f for k, v in value.items() for f in _fields(v, str(k).lower())]
    if isinstance(value, list):
        return [f for v in value for f in _fields(v, key)]
    return []


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()
