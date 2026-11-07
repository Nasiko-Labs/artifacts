"""Code-enforced gate on every write: one approval card binds one tool, one target and one content, and
allows exactly one call.

The three writes of a pull request each have their own card and their own `Approval`:
- branch: the tool, repository, new branch name, base branch and base commit;
- commit: the tool, repository, branch, file path, the approved file content (by hash) and message;
- pr:     the tool, repository, head branch, base branch, title and body.
A call naming any other tool, repository, branch, path, content, title or body is refused, and free text
outside the bound fields is refused too. The tool-name helpers decide what counts as a write.

Pure: decisions only, no I/O. The name helpers are adapted from the promise-tracker's send guard.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove", "trash",
    "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "rename", "upload", "insert",
    "write", "publish", "schedule", "share", "submit", "import", "cancel", "complete", "close", "assign",
    "approve", "transfer", "notify", "merge", "rerun", "dispatch", "trigger", "revert", "push", "lock", "unlock",
    "dismiss", "enable", "disable", "label", "comment", "acknowledge", "resolve", "snooze", "silence", "mute",
    "escalate", "commit",
})
READ_VERBS = frozenset({"get", "list", "search", "fetch", "find", "read", "download", "retrieve", "describe"})
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_SPACE = re.compile(r"\s+")

OWNER_KEYS = frozenset({"owner", "repo_owner", "org", "organization", "namespace"})
REPO_KEYS = frozenset({"repo", "repository", "repo_name", "repository_name"})
BRANCH_KEYS = frozenset({"ref", "branch", "branch_name", "new_branch", "head", "head_branch", "source_branch", "name"})
BASE_KEYS = frozenset({"base", "base_branch", "from_branch", "target_branch"})
SHA_KEYS = frozenset({"sha", "from_sha", "base_sha", "commit_sha"})
PATH_KEYS = frozenset({"path", "file_path", "filename"})
CONTENT_KEYS = frozenset({"content", "file_content", "contents"})
MESSAGE_KEYS = frozenset({"message", "commit_message"})
TITLE_KEYS = frozenset({"title"})
BODY_KEYS = frozenset({"body", "description", "text"})
FLAG_KEYS = frozenset({"draft", "maintainer_can_modify", "force"})


@dataclass(frozen=True)
class Approval:
    stage: str              # branch | commit | pr
    tool: str
    owner: str
    repo: str
    branch: str
    base: str = ""
    base_sha: str = ""
    path: str = ""
    content_hash: str = ""
    file_sha: str = ""
    message: str = ""
    title: str = ""
    body: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: object) -> Approval | None:
        if not isinstance(data, Mapping) or not data.get("stage") or not data.get("tool"):
            return None
        return Approval(**{f.name: str(data.get(f.name) or "") for f in fields(Approval)})


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    is_write: bool
    reason: str = ""


def name_tokens(tool_name: str) -> tuple[str, ...]:
    return tuple(t.lower() for t in _NON_ALNUM.split(_CAMEL.sub("_", tool_name)) if t)


def is_write_tool(tool_name: str) -> bool:
    tokens = name_tokens(tool_name)
    verbs = [t for t in tokens if t in WRITE_VERBS or t in READ_VERBS]
    if verbs and verbs[0] in READ_VERBS and not any(t in WRITE_VERBS - {"comment", "label", "commit"} for t in tokens):
        return False
    return any(t in WRITE_VERBS for t in tokens)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def check(name: str, arguments: Mapping[str, Any], approval: Approval | None) -> GuardDecision:
    if not is_write_tool(name):
        return GuardDecision(True, False)
    if approval is None:
        return _refused(f"Refused: {name} writes and has no approval of its own. Nothing was written.")
    if name.casefold() != approval.tool.casefold():
        return _refused(f"Refused: the approved write uses {approval.tool}, not {name}.")
    problem = _mismatch({str(k).casefold(): v for k, v in arguments.items()}, approval)
    return _refused(problem) if problem else GuardDecision(True, True)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(False, True, reason)


def _mismatch(args: Mapping[str, Any], a: Approval) -> str | None:
    def values(keys: frozenset[str]) -> list[str]:
        return [_text(v) for k, v in args.items() if k in keys]

    if any(v.casefold() != a.owner.casefold() for v in values(OWNER_KEYS)):
        return f"Refused: the write must go to {a.owner}/{a.repo}."
    repos = values(REPO_KEYS)
    if not repos or any(v.casefold() not in (a.repo.casefold(), f"{a.owner}/{a.repo}".casefold()) for v in repos):
        return f"Refused: the write must go to {a.owner}/{a.repo}."
    branches = [v.removeprefix("refs/heads/").split(":")[-1] for v in values(BRANCH_KEYS)]
    if not branches or any(b != a.branch for b in branches):
        return f"Refused: the write must name exactly the approved branch {a.branch}."
    if any(v != a.base for v in values(BASE_KEYS)) or (a.stage == "pr" and not values(BASE_KEYS)):
        return f"Refused: the base branch must be {a.base}."
    stage_check = {"branch": _branch_stage, "commit": _commit_stage, "pr": _pr_stage}.get(a.stage)
    if stage_check is None:
        return "Refused: unknown approval."
    problem = stage_check(values, a)
    if problem:
        return problem
    bound = OWNER_KEYS | REPO_KEYS | BRANCH_KEYS | BASE_KEYS | SHA_KEYS | PATH_KEYS | CONTENT_KEYS | MESSAGE_KEYS
    stray = [k for k, v in args.items() if k not in bound | TITLE_KEYS | BODY_KEYS | FLAG_KEYS and _SPACE.search(_text(v))]
    return f"Refused: this call carries text that was not approved ({', '.join(stray)})." if stray else None


def _branch_stage(values, a: Approval) -> str | None:
    if any(v != a.base_sha for v in values(SHA_KEYS)):
        return "Refused: the branch must start from the approved base commit."
    if values(PATH_KEYS) or values(CONTENT_KEYS) or values(TITLE_KEYS) or values(BODY_KEYS):
        return "Refused: creating the branch writes nothing else."
    return None


def _commit_stage(values, a: Approval) -> str | None:
    paths = values(PATH_KEYS)
    if not paths or any(p.lstrip("/") != a.path for p in paths):
        return f"Refused: the commit must change exactly {a.path}."
    contents = values(CONTENT_KEYS)
    if not contents or any(_content_digest(c) != a.content_hash for c in contents):
        return "Refused: the file content is not the approved diff."
    if any(_norm(m) != _norm(a.message) for m in values(MESSAGE_KEYS)):
        return "Refused: the commit message is not the approved one."
    if any(v != a.file_sha for v in values(SHA_KEYS)):
        return "Refused: the commit must replace the file version that was read."
    if values(TITLE_KEYS) or values(BODY_KEYS):
        return "Refused: the commit carries text that was not approved."
    return None


def _pr_stage(values, a: Approval) -> str | None:
    titles, bodies = values(TITLE_KEYS), values(BODY_KEYS)
    if not titles or any(_norm(t) != _norm(a.title) for t in titles):
        return "Refused: the pull request title is not the approved one."
    if any(_norm(b) != _norm(a.body) for b in bodies):
        return "Refused: the pull request body is not the approved one."
    if values(PATH_KEYS) or values(CONTENT_KEYS) or values(SHA_KEYS):
        return "Refused: opening the pull request writes nothing else."
    return None


def _content_digest(text: str) -> str:
    """The hash of the content as sent, or of its base64-decoded text when it is base64."""
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return content_hash(text)
    return content_hash(decoded) if decoded and base64.b64encode(decoded.encode()).decode() == text else content_hash(text)


def _text(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    return str(value)


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip()
