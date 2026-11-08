"""Pure: the write plan shown on the first card: an ordered list of single writes (create the branch, commit
each file, open the draft pull request), each executed only after its own approval. Each step has a digest
over the fields an earlier card fixed (tool, repository, base, branch, path, content hash), so a later step
can never drift from what card 1 showed. Title, body and commit message are edited on their own card.
Also the branch name (code: issue number, title slug, diff hash) and the commit message."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from diffing import FileChange, sha256

BRANCH_PREFIX = "nasiko/"
SLUG_CHARS = 40
FIXED_FIELDS = ("kind", "tool", "repo", "base", "branch", "path", "content_sha", "new_file")


@dataclass(frozen=True)
class Step:
    kind: str                 # branch | commit | pr
    tool: str
    repo: str                 # owner/name
    base: str
    branch: str
    path: str = ""
    content_sha: str = ""
    new_file: bool = False
    blob_sha: str = ""
    message: str = ""
    title: str = ""
    body: str = ""
    draft: bool = True

    def digest(self) -> str:
        fixed = {k: getattr(self, k) for k in FIXED_FIELDS}
        return hashlib.sha256(json.dumps(fixed, sort_keys=True).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Step:
        return Step(**{k: data[k] for k in Step.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class WritePlan:
    steps: tuple[Step, ...]
    done: int = 0             # steps executed so far
    stopped: str = ""         # why the sequence stopped early ("skipped", a failure, a guard refusal)

    @property
    def next_step(self) -> Step | None:
        return self.steps[self.done] if not self.stopped and self.done < len(self.steps) else None

    @property
    def finished(self) -> bool:
        return self.done >= len(self.steps)

    def advanced(self) -> WritePlan:
        return replace(self, done=self.done + 1)

    def stop(self, why: str) -> WritePlan:
        return replace(self, stopped=why)

    def with_step(self, index: int, step: Step) -> WritePlan:
        return replace(self, steps=(*self.steps[:index], step, *self.steps[index + 1:]))

    def to_dict(self) -> dict:
        return {"steps": [s.to_dict() for s in self.steps], "done": self.done, "stopped": self.stopped}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> WritePlan | None:
        if not data or not isinstance(data.get("steps"), list):
            return None
        return WritePlan(tuple(Step.from_dict(s) for s in data["steps"]), int(data.get("done") or 0),
                         str(data.get("stopped") or ""))


def slug(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", (title or "").encode("ascii", "ignore").decode().casefold())
    out = ""
    for word in words:
        candidate = f"{out}-{word}" if out else word
        if len(candidate) > SLUG_CHARS:
            break
        out = candidate
    return out or "change"


def branch_name(issue_number: int | None, title: str, diff: str) -> str:
    hash6 = hashlib.sha256(diff.encode("utf-8")).hexdigest()[:6]
    head = f"issue-{issue_number}-{slug(title)}" if issue_number is not None else f"fix-{slug(title)}"
    return f"{BRANCH_PREFIX}{head}-{hash6}"


def commit_message(title: str, issue_number: int | None, closes: bool) -> str:
    if issue_number is None:
        return title
    return f"{title}\n\n{'Closes' if closes else 'Refs'} #{issue_number}"


def build(tools: Mapping[str, str], repo: str, base: str, branch: str, items: Sequence[FileChange],
          blob_shas: Mapping[str, str], message: str, title: str, body: str) -> WritePlan:
    """`tools` = step kind → the write tool's real name (found by discovery)."""
    steps = [Step("branch", tools["branch"], repo, base, branch)]
    for change in sorted(items, key=lambda c: (c.new_file, c.path)):
        steps.append(Step("commit", tools["commit"], repo, base, branch, change.path, sha256(change.final),
                          change.new_file, "" if change.new_file else blob_shas.get(change.path, ""), message))
    steps.append(Step("pr", tools["pr"], repo, base, branch, title=title, body=body, draft=True))
    return WritePlan(tuple(steps))


def leftovers(plan: WritePlan) -> str:
    """What exists on the code host right now, said plainly."""
    executed = plan.steps[:plan.done]
    if not executed:
        return "Nothing was pushed."
    branch = executed[0].branch
    commits = [s for s in executed if s.kind == "commit"]
    total = sum(1 for s in plan.steps if s.kind == "commit")
    if any(s.kind == "pr" for s in executed):
        return f"Branch `{branch}` has all {total} file(s) committed and the draft pull request is open."
    if not commits:
        return f"Branch `{branch}` exists with no commits, and no pull request was opened."
    which = "all" if len(commits) == total else f"{len(commits)} of {total}"
    return f"Branch `{branch}` exists with {which} file(s) committed, and no pull request was opened."
