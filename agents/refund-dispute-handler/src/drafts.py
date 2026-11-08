"""Pure: the reply draft to the customer, reading the draft model's JSON, the code checks on the drafted text,
the "what do you want to tell the customer?" question, the approval question for the draft, and reading the
human's answers."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

import prompts
from case import load_json_object
from nasiko_hitl import question_metadata
from send_guard import Approval

SEND = "Send"
SKIP = "Skip"
USE_RECOMMENDATION = "Use the recommendation"
DIFFERENT_DENY = "Different decision: deny"
DIFFERENT_APPROVE = "Different decision: approve"
DECISION_HEADER = "What do you want to tell the customer?"
MAX_BODY_CHARS = 1000
_SEND_WORDS = frozenset({"send", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "stop", "denied", "deny",
                         "reject", "rejected", "decline", "declined", "nothing", "none"})
_HEADER_NAME_MAX = 120  # bytes; keeps the header inside the platform's 200-byte limit
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_MONEY = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?(?![\d])|(?<![\w.,])\d+\.\d{2}(?![\d])")
_ISSUED = re.compile(r"\b(?:approved|refunded)\b|\b(?:has|have|was)\s+been\s+(?:issued|processed|credited)\b"
                     r"|\bwe(?:'ve| have)?\s+(?:issued|processed)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(r"\b(?:fraud\w*|chargeback\w*|disput\w*|lawsuit|legal action|attorney)\b", re.IGNORECASE)

Decision = Literal["send", "skip", "edit"]


@dataclass(frozen=True)
class Draft:
    number: str      # "" (one reply per case)
    key: str         # the stable key the outcome is recorded under
    channel: str     # "email"
    to: str          # the checked customer address
    to_name: str
    subject: str
    body: str
    tool: str
    thread_ref: str
    link: str
    gist: str
    tier: str        # the decision the draft carries

    @property
    def who(self) -> str:
        return self.to_name or self.to

    @property
    def label(self) -> str:
        return f"Reply to {self.who}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Draft:
        return Draft(**{f.name: str(data.get(f.name) or "") for f in fields(Draft)})


class DraftText(BaseModel):
    subject: str = ""
    body: str


def parse_draft(text: str) -> DraftText | None:
    data = load_json_object(text)
    if data is None:
        return None
    try:
        draft = DraftText.model_validate({k: str(v).strip() for k, v in data.items()
                                          if k in ("subject", "body") and v is not None})
    except ValidationError:
        return None
    return draft if draft.body else None


def strip_links(body: str) -> str:
    """The reply carries no links: any URL the model wrote is removed."""
    return _URL.sub("", body)


def with_placeholder(body: str, decision: str, confirmed: bool) -> str:
    """Before the user confirms the refund in chat, an approve/partial reply always carries the placeholder."""
    if decision not in ("approve", "partial") or confirmed or prompts.REFUND_PLACEHOLDER in body:
        return body
    return f"{body.rstrip()}\n\n{prompts.REFUND_PLACEHOLDER}"


def body_problem(body: str, recipient: str, allowed_amounts: Collection[str], confirmed: bool) -> str | None:
    """Code checks on the drafted text: length, no other address, only checked amounts, no claim that money
    moved before the user confirmed it, no legal, fraud or dispute language."""
    if len(body) > MAX_BODY_CHARS:
        return "the draft was too long"
    if any(e.casefold() != recipient.casefold() for e in _EMAIL.findall(body)):
        return "the draft named an address that isn't the customer's"
    allowed = {a for amount in allowed_amounts for a in (amount, amount.replace(",", ""))}
    if any(m not in allowed and m.replace(",", "") not in allowed for m in _MONEY.findall(body)):
        return "the draft named an amount that isn't a checked one"
    if not confirmed and _ISSUED.search(body.replace(prompts.REFUND_PLACEHOLDER, "")):
        return "the draft said the refund was approved or issued before you confirmed it"
    if _FORBIDDEN.search(body):
        return "the draft used legal, fraud or dispute language"
    return None


def reply_draft(key: str, to: str, to_name: str, subject: str, body: str, tool: str, thread_ref: str,
                decision: str, gist: str) -> Draft:
    """Recipient comes from the checked customer, never from the draft model."""
    return Draft(number="", key=key, channel="email", to=to, to_name=to_name, subject=subject, body=body, tool=tool,
                 thread_ref=thread_ref, link="", gist=gist, tier=decision)


def decision_question(decision: str, summary: str) -> tuple[str, dict]:
    """After the recommendation: which decision the reply should carry (one question, single select)."""
    options = [(USE_RECOMMENDATION, summary)]
    if decision != "deny":
        options.append((DIFFERENT_DENY, "Draft a polite refusal under the policy."))
    if decision != "approve":
        options.append((DIFFERENT_APPROVE, "Draft an approval of the full refundable amount."))
    hitl = question_metadata(DECISION_HEADER, options, multi_select=False, allow_custom_input=True)
    text = (f"{DECISION_HEADER} Pick one, type an instruction for the reply, or say skip. I'll show the draft for "
            "your approval before anything is sent.")
    return text, hitl


def read_decision(answer: str, recommended: str) -> tuple[str | None, str]:
    """(the decision, an instruction) from the answer; (None, "") for skip."""
    text = (answer or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    if not text or text.casefold().rstrip(".!") in _SKIP_WORDS:
        return None, ""
    if first == USE_RECOMMENDATION:
        return recommended, "\n".join(text.splitlines()[1:]).strip()
    if first == DIFFERENT_DENY:
        return "deny", "\n".join(text.splitlines()[1:]).strip()
    if first == DIFFERENT_APPROVE:
        return "approve", "\n".join(text.splitlines()[1:]).strip()
    return recommended, text


def approval_for(draft: Draft, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this recipient, this subject, this body."""
    return Approval(action="reply email", target=f"{draft.who} (email)", draft=body or draft.body, tool=draft.tool,
                    recipient=draft.to, subject=draft.subject)


def approval_question(draft: Draft) -> tuple[str, dict]:
    """The pause text (everything that will be sent) and the structured question for the card."""
    name = draft.who if len(draft.who.encode("utf-8")) <= _HEADER_NAME_MAX else "the customer"
    hitl = question_metadata(f"Send this reply to {name}?", [(SEND, "Send exactly this text."),
                                                             (SKIP, "Don't send it.")],
                             multi_select=False, allow_custom_input=True)
    return describe(draft, draft.body) + "\n\nReply Send, Skip, or type your own text to send instead.", hitl


def describe(draft: Draft, body: str) -> str:
    lines = [f"Reply for {draft.gist}", f"To: {draft.who} <{draft.to}>", f"Via: {draft.tool}",
             f"Subject: {draft.subject}", f"Decision: {draft.tier}"]
    return "\n".join([*lines, "", body])


def interpret_answer(answer: str) -> tuple[Decision, str]:
    """Send / Skip / an edited text. Empty and unclear refusals are Skip: nothing goes out by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _SKIP_WORDS:
        return "skip", ""
    if word in _SEND_WORDS:
        return "send", ""
    return "edit", text
