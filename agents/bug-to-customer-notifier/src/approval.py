"""Pure: a drafted reply, its approval question (one ticket per pause), and reading the answer.

Options: `Send and solve` / `Send, keep open` / `Skip`; typed text is the edited reply and is sent kept
open (a typed answer cannot also pick an option). Empty and unclear refusals are Skip: nothing is posted
by default."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from nasiko_hitl import MAX_QUESTION_HEADER_LEN, question_metadata
from pick import clip
from send_guard import Approval

SEND_SOLVE = "Send and solve"
SEND_KEEP = "Send, keep open"
SKIP = "Skip"
SOLVED_STATUS = "solved"
_SOLVE_WORDS = frozenset({"send and solve", "send & solve", "solve", "send and close"})
_KEEP_WORDS = frozenset({"send, keep open", "send keep open", "send", "keep open", "yes", "y", "ok", "okay",
                         "approve", "approved", "authorized", "send it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "stop", "denied", "deny", "reject",
                         "rejected", "decline", "declined"})

Decision = Literal["solve", "keep", "skip", "edit"]


@dataclass(frozen=True)
class Draft:
    ticket_id: str
    customer: str
    subject: str
    body: str
    tools: tuple[str, ...] = ()
    ticket_url: str = ""
    warnings: tuple[str, ...] = field(default=())   # an edited reply that breaks a content rule

    def to_dict(self) -> dict:
        return {"ticket_id": self.ticket_id, "customer": self.customer, "subject": self.subject, "body": self.body,
                "tools": list(self.tools), "ticket_url": self.ticket_url, "warnings": list(self.warnings)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Draft:
        return Draft(ticket_id=str(data.get("ticket_id") or ""), customer=str(data.get("customer") or ""),
                     subject=str(data.get("subject") or ""), body=str(data.get("body") or ""),
                     tools=tuple(str(t) for t in data.get("tools") or ()), ticket_url=str(data.get("ticket_url") or ""),
                     warnings=tuple(str(w) for w in data.get("warnings") or ()))


def approval_for(draft: Draft, decision: Decision, body: str | None = None) -> Approval:
    """The writes the human approved: this ticket, this public reply, and (only on solve) status solved."""
    return Approval(target_id=draft.ticket_id, body=body or draft.body,
                    status=SOLVED_STATUS if decision == "solve" else "", tools=draft.tools)


def approval_question(draft: Draft) -> tuple[str, dict]:
    """The pause text (everything that will be posted) and the structured question for the card."""
    header = clip(f"Post this reply on ticket #{draft.ticket_id} ({draft.customer})?", MAX_QUESTION_HEADER_LEN)
    hitl = question_metadata(header, [
        (SEND_SOLVE, "Post this public reply and set the ticket to solved."),
        (SEND_KEEP, "Post this public reply; leave the ticket status as it is."),
        (SKIP, "Don't post anything on this ticket; move on."),
    ], multi_select=False, allow_custom_input=True)
    hint = (f"Reply {SEND_SOLVE}, {SEND_KEEP} or {SKIP}, or type your own text to post instead (it is posted and "
            "the ticket kept open).")
    return f"{describe(draft, draft.body)}\n\n{hint}", hitl


def describe(draft: Draft, body: str) -> str:
    ticket = f"#{draft.ticket_id}" + (f" ({draft.ticket_url})" if draft.ticket_url else "")
    lines = [f"Public reply on ticket {ticket}, customer {draft.customer}", f"Ticket subject: {draft.subject or '-'}",
             f"Via: {', '.join(draft.tools) or '-'}"]
    warning = [f"Warning: this text breaks a reply rule: {'; '.join(draft.warnings)}."] if draft.warnings else []
    return "\n".join([*lines, *warning, "", body])


def interpret_answer(answer: str) -> tuple[Decision, str]:
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _SKIP_WORDS:
        return "skip", ""
    if word in _SOLVE_WORDS:
        return "solve", ""
    if word in _KEEP_WORDS:
        return "keep", ""
    return "edit", text
