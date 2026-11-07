"""Pure: one pull-request plan (branch, commit, pull request) for one `lengthen_for` proposal, built in code
from the located definition, plus its three approval cards.

Every card shows exactly what its one write will do; the first card also shows the whole end state
(path, diff, title, body) before anything is written.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace

import render
from nasiko_hitl import MAX_QUESTION_HEADER_LEN, question_metadata
from patch import Patch
from send_guard import Approval

APPROVE, SKIP = "Approve", "Skip"
STAGES = ("branch", "commit", "pr")
BRANCH_PREFIX = "alert-tuner/"
_BRANCH_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


@dataclass(frozen=True)
class Plan:
    alert: str
    owner: str
    repo: str
    base: str
    base_sha: str
    branch: str
    patch: Patch
    title: str
    body: str
    message: str
    tools: Mapping[str, str] = field(default_factory=dict)   # stage -> tool name

    @property
    def repo_full(self) -> str:
        return f"{self.owner}/{self.repo}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["patch"] = self.patch.to_dict()
        data["tools"] = dict(self.tools)
        return data

    @staticmethod
    def from_dict(data: Mapping) -> Plan:
        values = {k: str(data.get(k) or "") for k in ("alert", "owner", "repo", "base", "base_sha", "branch", "title",
                                                       "body", "message")}
        return Plan(**values, patch=Patch.from_dict(dict(data["patch"])),
                    tools={str(k): str(v) for k, v in (data.get("tools") or {}).items()})

    def shown(self) -> dict:
        """What card 1 showed and later cards must still match."""
        return {"branch": self.branch, "base": self.base, "path": self.patch.path, "content": self.patch.digest}


def slug(text: str, limit: int = 40) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").casefold().replace("<*>", " "))
    return "-".join(words)[:limit].strip("-") or "alert"


def valid_branch(name: str) -> bool:
    return bool(_BRANCH_OK.match(name)) and ".." not in name and not name.endswith((".lock", "/", ".")) and "//" not in name


def build(alert: Mapping, proposal: Mapping, owner: str, repo: str, base: str, base_sha: str, p: Patch,
          rule: str) -> Plan:
    branch = f"{BRANCH_PREFIX}{slug(rule or alert['alert'])}-for-{p.new_value}"
    title = f"Lengthen `for` of {rule} from {p.old_value} to {p.new_value}"
    return Plan(alert=alert["alert"], owner=owner, repo=repo, base=base, base_sha=base_sha, branch=branch, patch=p,
                title=title, body=pr_body(alert, proposal, p), message=f"{title} (alert-tuner)")


def pr_body(alert: Mapping, proposal: Mapping, p: Patch) -> str:
    checked = alert["assessed"]
    lines = [
        (f"Alert {render.code(alert['alert'])} fired {alert['fired']} times in the window; {alert['zero_action']} of "
         f"{checked} checked incidents had no human action ({alert['zero_action_pct']}%), {alert['ack_count']} acks."),
        (f"Zero-action incidents self-resolved in a median of {render.minutes(alert['median_duration_min'])} "
         f"(p90 {render.minutes(alert['p90_duration_min'])})."),
        (f"Raising `for` from {p.old_value} to {p.new_value} would have suppressed {proposal['avoided']} of "
         f"{proposal['of']} zero-action firings."),
        f"Evidence: {render.evidence_line(alert['evidence'])}",
        f"Change: only {render.code(p.path + ':' + str(p.line))}.",
        "Computed from incident history by the alert tuner; no other file content changes.",
    ]
    return "\n\n".join(lines)


def approval(plan: Plan, stage: str) -> Approval:
    common = {"stage": stage, "tool": plan.tools.get(stage, ""), "owner": plan.owner, "repo": plan.repo,
              "branch": plan.branch, "base": plan.base}
    if stage == "branch":
        return Approval(**common, base_sha=plan.base_sha)
    if stage == "commit":
        return Approval(**{**common, "base": ""}, path=plan.patch.path, content_hash=plan.patch.digest,
                        file_sha=plan.patch.file_sha, message=plan.message)
    return Approval(**common, title=plan.title, body=plan.body)


def _header(text: str) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= MAX_QUESTION_HEADER_LEN else raw[:MAX_QUESTION_HEADER_LEN - 3].decode("utf-8", "ignore") + "..."


def card(plan: Plan, stage: str, note: str = "") -> tuple[str, dict]:
    """The pause text and its structured question (Approve / Skip; typed text is an edit instruction)."""
    if stage == "branch":
        header = f"Create branch `{plan.branch}` in `{plan.repo_full}` from `{plan.base}`?"
        body = (f"Step 1 of 3: create branch `{plan.branch}` from `{plan.base}` (commit {plan.base_sha[:12]}).\n"
                f"Via: {plan.tools.get('branch')}\n\nThe whole plan (each later step has its own card):\n"
                f"- File: `{plan.patch.path}` line {plan.patch.line}\n- Diff:\n```diff\n{plan.patch.diff}\n```\n"
                f"- Pull request title: {plan.title}\n- Pull request body:\n\n{plan.body}")
        edit = "type a different branch name to use instead"
    elif stage == "commit":
        header = f"Commit `{plan.patch.path}` to `{plan.branch}`?"
        body = (f"Step 2 of 3: commit to `{plan.branch}` in `{plan.repo_full}`.\nVia: {plan.tools.get('commit')}\n"
                f"Message: {plan.message}\n\n```diff\n{plan.patch.diff}\n```")
        edit = 'type another duration (e.g. "use 30m") to change the new value'
    else:
        header = f"Open PR `{plan.title}` from `{plan.branch}` to `{plan.base}`?"
        body = (f"Step 3 of 3: open a pull request in `{plan.repo_full}`.\nVia: {plan.tools.get('pr')}\n\n"
                f"Title: {plan.title}\n\nBody:\n\n{plan.body}")
        edit = 'type "title: ..." or "body: ..." to edit them'
    hitl = question_metadata(_header(header), [(APPROVE, "Make exactly this one write."),
                                               (SKIP, "Don't write this; stop here.")], allow_custom_input=True)
    text = "\n\n".join(x for x in (note, body, f"Reply Approve or Skip, or {edit}.") if x)
    return text, hitl


def edit_branch(plan: Plan, text: str) -> Plan | str:
    name = text.strip().strip("`'\"")
    name = name if name.startswith(BRANCH_PREFIX) else BRANCH_PREFIX + name
    if not valid_branch(name) or " " in name:
        return f"`{text.strip()[:60]}` is not a branch name I can use (letters, digits, `-`, `_`, `.`, `/`)."
    return replace(plan, branch=name)


def edit_pr(plan: Plan, text: str) -> Plan | str:
    stripped = text.strip()
    lower = stripped.casefold()
    if lower.startswith("title:"):
        title = stripped[6:].strip()
        return replace(plan, title=title) if title and "\n" not in title else "The title must be one non-empty line."
    if lower.startswith("body:"):
        body = stripped[5:].strip()
        return replace(plan, body=body) if body else "The body can't be empty."
    if "\n" not in stripped and len(stripped) <= 120:
        return replace(plan, title=stripped)
    return replace(plan, body=stripped)


def copyable(plan: Plan, reason: str) -> str:
    return (f"{reason} Here is the change to make by hand in `{plan.repo_full}`:\n\n```diff\n{plan.patch.diff}\n```\n\n"
            f"Pull request title: {plan.title}\n\nPull request body:\n\n{plan.body}")
