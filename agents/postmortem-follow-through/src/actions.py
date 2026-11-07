"""Pure: one write the user is asked to approve (a nudge to one owner, or one reopen), the approval
question for it, the exact approval that results from the answer, and reading the answer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from nasiko_hitl import question_metadata
from send_guard import Approval, WriteSpec

SEND, REOPEN, SKIP = "Send", "Reopen", "Skip"
_YES = frozenset({"send", "reopen", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send it",
                  "reopen it", "go", "go ahead", "do it"})
_NO = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "stop", "denied", "deny", "reject",
                 "rejected", "decline", "declined", "don't", "dont"})
_HEADER_NAME_MAX = 120  # bytes; keeps the header inside the platform's 200-byte limit
# The write whose body is the approved text, per kind of action.
_TEXT_WRITE = {"nudge": "message", "reopen": "comment"}

Decision = Literal["approve", "skip", "edit"]


@dataclass(frozen=True)
class Action:
    kind: str                       # "nudge" | "reopen"
    ref: str                        # the owner's email, or the ticket key
    who: str                        # the owner's name, or the ticket key
    details: str                    # what is shown above the text: recipient / tools, or the status change
    editable: str                   # the nudge's opening line, or the reopen comment
    fixed: str                      # the nudge's item lines (built in code), or ""
    writes: tuple[WriteSpec, ...]   # the text write's body is filled in at approval time
    row_keys: tuple[str, ...]       # ticket keys this action covers
    warning: str = ""

    def text(self, editable: str | None = None) -> str:
        opening = (editable if editable is not None else self.editable).strip()
        return f"{opening}\n\n{self.fixed}" if self.fixed else opening

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "who": self.who, "details": self.details,
                "editable": self.editable, "fixed": self.fixed, "writes": [w.to_dict() for w in self.writes],
                "row_keys": list(self.row_keys), "warning": self.warning}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Action:
        return Action(kind=str(data["kind"]), ref=str(data["ref"]), who=str(data.get("who") or ""),
                      details=str(data.get("details") or ""), editable=str(data.get("editable") or ""),
                      fixed=str(data.get("fixed") or ""),
                      writes=tuple(WriteSpec.from_dict(w) for w in data.get("writes") or ()),
                      row_keys=tuple(str(k) for k in data.get("row_keys") or ()),
                      warning=str(data.get("warning") or ""))


def approval_for(action: Action, edited: str | None = None) -> Approval:
    """The writes the human approved; typed text replaces only the editable part."""
    body = action.text(edited)
    writes = tuple(replace(w, body=body) if w.kind == _TEXT_WRITE[action.kind] else w for w in action.writes)
    return Approval(action=action.kind, target=action.who, writes=writes)


def header(action: Action) -> str:
    name = action.who if len(action.who.encode("utf-8")) <= _HEADER_NAME_MAX else "this owner"
    return f"Send this nudge to {name}?" if action.kind == "nudge" else f"Reopen {name}?"


def describe(action: Action, edited: str | None = None) -> str:
    lines = [action.warning] if action.warning else []
    label = "Message" if action.kind == "nudge" else "Comment"
    return "\n".join([*lines, action.details, "", f"{label}:", action.text(edited)])


def approval_question(action: Action) -> tuple[str, dict]:
    """The pause text (everything that will be written) and the structured question for the card."""
    if action.kind == "nudge":
        options = [(SEND, "Send exactly this message."), (SKIP, "Don't send it; move on.")]
        hint = "Reply Send, Skip, or type your own opening line (the item list stays)."
    else:
        options = [(REOPEN, "Reopen the ticket and add exactly this comment."), (SKIP, "Leave it closed.")]
        hint = "Reply Reopen, Skip, or type your own comment to add instead."
    hitl = question_metadata(header(action), options, multi_select=False, allow_custom_input=True)
    return f"{describe(action)}\n\n{hint}", hitl


def interpret_answer(answer: str) -> tuple[Decision, str]:
    """Approve / Skip / edited text. Empty and unclear refusals are Skip: nothing is written by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "approve", ""
    return "edit", text
