"""Pure: what failed run the user means, read from their message in code.

A target is a run (URL or id), a job (URL), a pull request (URL, `owner/repo#N`, "PR N") or "the
latest failure on <branch>". The repository comes from the message, else from the one remembered for
this conversation; a pull request number without any repository is kept as pending and the user is
asked once for the repository.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace
from typing import Literal

Kind = Literal["run", "job", "pr", "latest"]

_RUN_URL = re.compile(r"https?://[^\s/]+/([\w.-]+)/([\w.-]+)/actions/runs/(\d+)(?:/job/(\d+))?", re.IGNORECASE)
_PR_URL = re.compile(r"https?://[^\s/]+/([\w.-]+)/([\w.-]+)/pulls?/(\d+)", re.IGNORECASE)
_REPO_HASH = re.compile(r"(?<![\w/.-])([\w.-]+)/([\w.-]+)#(\d+)\b")
_PR_WORD = re.compile(r"\b(?:PR|pull request|pull)\s*#?\s*(\d+)\b", re.IGNORECASE)
_RUN_WORD = re.compile(r"\brun\s*(?:id\s*)?#?\s*(\d{5,})\b", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w/.:-])([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*[A-Za-z0-9])(?![\w/#-])")
_ON_BRANCH = re.compile(r"\bon\s+(?:the\s+)?(?:branch\s+)?[`'\"]?([\w./-]+?)[`'\"]?(?:\s+(?:in|of|for)\b|[\s,.?!]*$)",
                        re.IGNORECASE)
_LATEST = re.compile(r"\b(?:latest|last|most recent|newest)\b", re.IGNORECASE)
_JOB_NAME = re.compile(r"\b(?:check|analy[sz]e|look at)\s+(?:the\s+)?job\s+[`'\"]?([^`'\"\n]+?)[`'\"]?\s*[.?!]?$", re.IGNORECASE)
_NOT_BRANCHES = frozenset({"pr", "it", "this", "that", "the", "a", "ci", "my", "our"})
DEFAULT_BRANCH = "main"


@dataclass(frozen=True)
class Target:
    kind: Kind
    owner: str = ""
    repo: str = ""
    number: str = ""      # pull request number
    run_id: str = ""
    job_id: str = ""
    branch: str = ""
    job_name: str = ""    # "check job <name>": analyse that job of the same run

    @property
    def repo_full(self) -> str:
        return f"{self.owner}/{self.repo}" if self.owner and self.repo else ""

    @property
    def has_repo(self) -> bool:
        return bool(self.owner and self.repo)

    def describe(self) -> str:
        where = self.repo_full or "an unnamed repository"
        if self.kind == "pr":
            return f"pull request #{self.number} in {where}"
        if self.kind == "job":
            return f"job {self.job_id} of run {self.run_id} in {where}"
        if self.kind == "run":
            return f"run {self.run_id} in {where}"
        return f"the latest failed run on branch {self.branch or DEFAULT_BRANCH} in {where}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: object) -> Target | None:
        if not isinstance(data, dict) or data.get("kind") not in ("run", "job", "pr", "latest"):
            return None
        return Target(**{f.name: str(data.get(f.name) or "") for f in fields(Target)})


def parse(message: str, remembered_repo: str = "") -> Target | None:
    """The target named in the message (repository filled from memory when absent), or None."""
    text = message or ""
    found = _explicit(text)
    if found is None:
        return None
    if found.has_repo:
        return found
    owner, repo = split_repo(repo_in(text) or remembered_repo)
    return replace(found, owner=owner, repo=repo)


def _explicit(text: str) -> Target | None:
    if m := _RUN_URL.search(text):
        owner, repo, run_id, job_id = m.groups()
        return Target("job" if job_id else "run", owner, repo, run_id=run_id, job_id=job_id or "")
    if m := _PR_URL.search(text):
        return Target("pr", m.group(1), m.group(2), number=m.group(3))
    if m := _REPO_HASH.search(text):
        return Target("pr", m.group(1), m.group(2), number=m.group(3))
    if m := _PR_WORD.search(text):
        return Target("pr", number=m.group(1))
    if m := _RUN_WORD.search(text):
        return Target("run", run_id=m.group(1))
    if _LATEST.search(text) and re.search(r"\bfail", text, re.IGNORECASE):
        return Target("latest", branch=branch_in(text) or DEFAULT_BRANCH)
    return None


def repo_in(text: str) -> str:
    """`owner/repo` named in free text (not part of a URL or a path), or ""."""
    for m in _REPO.finditer(text or ""):
        owner, repo = m.groups()
        if "." in owner or repo.endswith((".py", ".ts", ".js", ".go", ".rs", ".java")):
            continue
        return f"{owner}/{repo}"
    return ""


def branch_in(text: str) -> str:
    m = _ON_BRANCH.search(text or "")
    if not m:
        return ""
    branch = m.group(1).strip("/.")
    return "" if branch.casefold() in _NOT_BRANCHES or "/" in branch and branch.count("/") > 2 else branch


def job_name_in(text: str) -> str:
    m = _JOB_NAME.search((text or "").strip())
    return m.group(1).strip() if m else ""


def split_repo(full: str) -> tuple[str, str]:
    owner, _, repo = (full or "").partition("/")
    return (owner, repo) if owner and repo else ("", "")


def with_repo(target: Target, full: str) -> Target:
    owner, repo = split_repo(full)
    return replace(target, owner=owner, repo=repo) if owner else target


def from_model(data: dict, remembered_repo: str = "") -> Target | None:
    """A target the intake model proposed; every field is re-checked by the same patterns."""
    full = str(data.get("repo") or "")
    if full and not _REPO.fullmatch(full):
        full = ""
    full = full or remembered_repo
    pr, run = str(data.get("pr") or ""), str(data.get("run_id") or "")
    branch = str(data.get("branch") or "")
    owner, repo = split_repo(full)
    if pr.isdigit():
        return Target("pr", owner, repo, number=pr)
    if run.isdigit():
        return Target("run", owner, repo, run_id=run)
    if branch and re.fullmatch(r"[\w./-]{1,100}", branch):
        return Target("latest", owner, repo, branch=branch)
    return None
