"""Code-enforced gate on every write (own copy of the kit's send guard, extended with the write plan).

One approval authorises exactly one call, and only the NEXT unexecuted step of the plan shown on card 1:
the same tool, the same repository, branch, base and path, file content whose SHA-256 equals the planned
one, `draft: true`, and title/body/message equal to the card (case and whitespace aside). A different
branch, an extra or changed file, a non-draft or second pull request, a merge/close/delete tool, or a
second write on one approval is refused before the gateway. Writing to the base branch is refused always.

Pure: decisions only, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import argmap
from diffing import sha256
from discovery import name_tokens
from nasiko_mcp import McpTool
from writeplan import WritePlan

FORBIDDEN_VERBS = frozenset({"merge", "delete", "remove", "close", "force", "revert", "reset", "transfer"})
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Approval:
    step: int
    digest: str

    def to_dict(self) -> dict:
        return {"step": self.step, "digest": self.digest}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def _norm(value: Any) -> str:
    return _SPACES.sub(" ", str(value or "")).strip().casefold()


def check(plan: WritePlan, approval: Approval | None, tool: McpTool, args: Mapping[str, Any],
          content: str = "") -> Decision:
    if approval is None:
        return Decision(False, "no approval for this write")
    step = plan.next_step
    if step is None or approval.step != plan.done:
        return Decision(False, "an approval covers only the next step of the plan")
    if approval.digest != step.digest():
        return Decision(False, "the step differs from the plan shown on the first card")
    if set(name_tokens(tool.name)) & FORBIDDEN_VERBS:
        return Decision(False, f"{tool.name} merges, closes or deletes; this agent never does that")
    if tool.name != step.tool:
        return Decision(False, f"the approved step uses {step.tool}, not {tool.name}")
    seen = argmap.read(tool, step.kind, args)
    if seen.get("repo") != step.repo:
        return Decision(False, f"the call targets {seen.get('repo')}, not {step.repo}")
    problem = _mismatch(step.kind, seen, step, content)
    return Decision(False, problem) if problem else Decision(True)


def _mismatch(kind: str, seen: Mapping[str, Any], step, content: str) -> str | None:
    if kind == "branch":
        if seen.get("branch") != step.branch or step.branch == step.base:
            return f"the branch must be {step.branch}"
        return None
    if kind == "commit":
        if seen.get("path") != step.path:
            return f"the approved commit is {step.path}, not {seen.get('path')}"
        if seen.get("branch") != step.branch or seen.get("branch") == step.base:
            return f"commits go to {step.branch} only, never the base branch"
        body = seen.get("content")
        if not isinstance(body, str) or sha256(body) != step.content_sha or sha256(content) != step.content_sha:
            return f"the content of {step.path} is not the content shown on the card"
        if _norm(seen.get("message")) != _norm(step.message):
            return "the commit message is not the approved one"
        return None
    if kind == "pr":
        if seen.get("head") != step.branch or seen.get("base") != step.base:
            return f"the pull request must go from {step.branch} to {step.base}"
        if seen.get("draft") is not True:
            return "pull requests are always opened as drafts"
        if _norm(seen.get("title")) != _norm(step.title) or _norm(seen.get("body")) != _norm(step.body):
            return "the title or body is not the approved one"
        return None
    return f"unknown step {kind}"
