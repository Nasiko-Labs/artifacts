"""The upgrade plan and its cards (branch, one commit per changed manifest, PR), built in code.

The plan holds everything a card shows: the repository and its base branch (from tool output, never
from the user's words), each manifest as fetched with its verified one-line edit (bump.py), and the PR
text. The diff is never editable. Each card's `Approval` binds its one tool, its target and its content
for the send guard.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

from nasiko_hitl import question_metadata
from send_guard import Approval

SKIP, APPROVE = "Skip", "Approve"
_BRANCH_NAME = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)(?!.*@\{)[A-Za-z0-9._/-]{1,100}(?<![/.])$")
_YES = frozenset({"approve", "approved", "yes", "y", "ok", "okay", "go", "do it", "authorized", "create", "commit",
                  "open", "open pr"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "deny", "denied", "reject", "decline", "don't", "dont"})


@dataclass(frozen=True)
class Plan:
    owner: str
    repo: str
    base: str
    base_sha: str
    package: str
    ecosystem: str
    target: str
    advisory_id: str = ""
    summary: str = ""
    major: bool = False
    files: tuple = ()             # dicts: path, sha, content, diff, old_line, new_line, status, declared
    problems: tuple = ()          # declarations that could not be edited (copy-only lines)
    branch: str = ""
    message_override: str = ""
    pr_title: str = ""
    pr_body_override: str = ""
    tools: dict = field(default_factory=dict)   # branch | commit | pr → tool name
    bound: dict = field(default_factory=dict)   # what the branch card showed: branch, base, files

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def can_pr(self) -> bool:
        return bool(self.files) and all(self.tools.get(k) for k in ("branch", "commit", "pr"))

    @property
    def title(self) -> str:
        suffix = f" ({self.advisory_id})" if self.advisory_id else ""
        return self.pr_title or f"Bump {self.package} to {self.target}{suffix}"

    def commit_message(self, index: int) -> str:
        if self.message_override:
            return self.message_override
        path = self.files[index]["path"] if index < len(self.files) else ""
        return f"Bump {self.package} to {self.target} in {path}"

    @property
    def pr_body(self) -> str:
        if self.pr_body_override:
            return self.pr_body_override
        head = f"Advisory {self.advisory_id}" if self.advisory_id else "Advisory"
        lines = [f"{head}: {self.summary}" if self.summary else head, ""]
        lines += [f"- `{f['path']}`: `{self.package}` {f['declared']} -> {self.target} ({f['status'].replace('_', ' ')})"
                  for f in self.files]
        if self.major:
            lines.append(f"- {self.target} is a major version bump: check the changelog.")
        lines += ["", "The lockfile is not updated: run the package manager to regenerate it.",
                  "Tests were not run by this change."]
        return "\n".join(lines)

    def binding(self) -> dict:
        digests = [[f["path"], hashlib.sha256(f["content"].encode()).hexdigest()] for f in self.files]
        return {"branch": self.branch, "base": self.base, "files": digests}

    def to_dict(self) -> dict:
        data = asdict(self)
        return {**data, "files": [dict(f) for f in self.files], "problems": list(self.problems)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Plan:
        names = {f.name for f in fields(Plan)}
        values = {k: v for k, v in dict(data).items() if k in names}
        values["files"] = tuple(dict(f) for f in values.get("files") or ())
        values["problems"] = tuple(values.get("problems") or ())
        return Plan(**values)


def branch_name(package: str, target: str) -> str:
    slug = re.sub(r"[^a-z0-9.]+", "-", package.casefold()).strip("-.")[:50] or "package"
    version = re.sub(r"[^A-Za-z0-9.]+", "-", target).strip("-.")[:30]
    return f"deps/{slug}-{version}"


def valid_branch(name: str) -> bool:
    return bool(_BRANCH_NAME.match(name or ""))


def approval(plan: Plan, kind: str, index: int = 0) -> Approval:
    values: dict[str, str]
    alternates: dict[str, tuple[str, ...]] = {}
    if kind == "branch":
        values, alternates = {"ref": plan.branch, "from": plan.base_sha or plan.base}, {"from": (plan.base,)}
    elif kind == "commit":
        f = plan.files[index]
        values = {"branch": plan.branch, "path": f["path"], "content": f["content"],
                  "message": plan.commit_message(index), "sha": f.get("sha") or ""}
    else:
        values = {"head": plan.branch, "base": plan.base, "title": plan.title, "body": plan.pr_body}
    return Approval(kind, plan.tools.get(kind, ""), plan.owner, plan.repo, values, alternates)


_HINTS = {"branch": "Reply Approve or Skip, or type a different branch name.",
          "commit": "Reply Approve or Skip, or type `message: <text>` to change the commit message (the diff is "
                    "not editable).",
          "pr": "Reply Approve or Skip, or type `title: <text>` or a new PR body."}
_SKIP_HELP = {"branch": "Write nothing.", "commit": "Stop here; the branch stays without this commit.",
              "pr": "Stop here; no pull request is opened."}


def card(plan: Plan, stage: str, index: int = 0, preface: str = "") -> tuple[str, dict]:
    """(text, question metadata) for one card."""
    header, text = _card_text(plan, stage, index)
    hitl = question_metadata(_cut(header), [(APPROVE, "Make exactly this one write."), (SKIP, _SKIP_HELP[stage])])
    return "\n\n".join(x for x in (preface, text, _HINTS[stage]) if x), hitl


def _card_text(plan: Plan, stage: str, index: int) -> tuple[str, str]:
    if stage == "branch":
        steps = [f"1. Create branch `{plan.branch}` from `{plan.base}`"]
        steps += [f"{i}. Commit `{f['path']}`:\n```diff\n{f['diff']}\n```" for i, f in enumerate(plan.files, 2)]
        steps.append(f"{len(plan.files) + 2}. Open PR `{plan.title}`:\n\n{plan.pr_body}")
        extra = ["", "Not editable here: " + "; ".join(plan.problems)] if plan.problems else []
        text = "Plan (each step gets its own card):\n" + "\n".join(steps + extra)
        return f"Create branch `{plan.branch}` in `{plan.full}` from `{plan.base}`?", text
    if stage == "commit":
        f = plan.files[index]
        text = f"Commit message: {plan.commit_message(index)}\n\n```diff\n{f['diff']}\n```"
        return f"Commit `{f['path']}` to `{plan.branch}`?", text
    text = f"Pull request in {plan.full}\nTitle: {plan.title}\n\n{plan.pr_body}"
    return f"Open PR `{plan.title}` from `{plan.branch}` to `{plan.base}`?", text


def _cut(header: str) -> str:
    data = header.encode()
    return header if len(data) <= 200 else data[:196].decode(errors="ignore") + "...?"


def interpret(answer: str) -> tuple[str, str]:
    """(approve | skip | edit, text). Empty and unclear refusals are skip: nothing is written by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "approve", ""
    return "edit", text


def apply_edit(plan: Plan, stage: str, text: str) -> tuple[Plan, str]:
    """The plan with the edit applied, or the unchanged plan and why it could not be applied."""
    if stage == "branch":
        name = text.strip().strip("`").removeprefix("refs/heads/")
        if not valid_branch(name):
            return plan, f"`{name[:60]}` isn't a valid branch name, so the card is unchanged."
        return replace(plan, branch=name), ""
    if stage == "commit":
        m = re.match(r"^message\s*:\s*(.+)$", text.strip(), re.IGNORECASE | re.DOTALL)
        if not m:
            return plan, ("Here I can only change the commit message (type `message: <text>`); the diff is not "
                          "editable, so the card is unchanged.")
        return replace(plan, message_override=" ".join(m.group(1).split())[:200]), ""
    m = re.match(r"^title\s*:\s*(.+)$", text.strip(), re.IGNORECASE)
    if m:
        return replace(plan, pr_title=" ".join(m.group(1).split())[:200]), ""
    return replace(plan, pr_body_override=text.strip()), ""


def copy_text(plan: Plan) -> str:
    """Everything the user needs to make the change by hand (no write tool, or a block stance)."""
    out = [f"Branch: {plan.branch} (from {plan.base or 'the default branch'})"]
    out += [f"```diff\n{f['diff']}\n```" for f in plan.files]
    out += [f"- {p}" for p in plan.problems]
    out.append(f"PR title: {plan.title}\n\n{plan.pr_body}")
    return "\n\n".join(out)
