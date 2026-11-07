"""Pure: a drafted recovery email or chat digest, reading the draft model's JSON, the code checks on the
drafted text, the approval question for one draft, and reading the human's answer to it."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

import prompts
import scoring
from failures import Failure, load_json_object
from nasiko_hitl import question_metadata
from send_guard import Approval

SEND = "Send"
POST = "Post"
SKIP = "Skip"
MAX_BODY_CHARS = 900
DIGEST_KEY = "digest"
_SEND_WORDS = frozenset({"send", "post", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send it",
                         "post it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "stop", "denied", "deny",
                         "reject", "rejected", "decline", "declined", "don't post", "dont post"})
_HEADER_NAME_MAX = 120  # bytes; keeps the header inside the platform's 200-byte limit
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_MONEY = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})*(?:\.\d{2})(?![\d])|(?<![\w.,])\d+\.\d{2}(?![\d])")
_PROMISES = re.compile(r"\b(?:waive[ds]?|waiving|discount\w*|refund\w*|extension|extend\w*|free month|fraud\w*|"
                       r"stolen|credit (?:you|your account))\b", re.IGNORECASE)

Decision = Literal["send", "skip", "edit"]


@dataclass(frozen=True)
class Draft:
    number: str      # the list number ("" for the digest)
    key: str         # the stable key outcomes are recorded under
    channel: str     # "email" | "chat"
    to: str          # the customer's address, or the chat channel
    to_name: str
    subject: str
    body: str
    tool: str
    thread_ref: str
    link: str
    gist: str
    tier: str

    @property
    def who(self) -> str:
        return self.to_name or self.to

    @property
    def label(self) -> str:
        return f"#{self.number}" if self.number else f"Digest to #{self.to.lstrip('#')}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Draft:
        return Draft(**{f.name: str(data.get(f.name) or "") for f in fields(Draft)})


class DraftText(BaseModel):
    """What the draft model wrote, before code decides whether it can be sent."""

    subject: str = ""
    body: str
    tool: str | None = None


def parse_draft(text: str) -> DraftText | None:
    data = load_json_object(text)
    if data is None:
        return None
    try:
        draft = DraftText.model_validate({k: (str(v).strip() if v is not None else None) for k, v in data.items()
                                          if k in ("subject", "body", "tool")})
    except ValidationError:
        return None
    return draft if draft.body else None


def clean_links(body: str, allowed: Collection[str]) -> str:
    """Every URL not given in the record is replaced by the update-link placeholder."""
    keep = {a.rstrip("/.,") for a in allowed if a}
    return _URL.sub(lambda m: m.group(0) if m.group(0).rstrip("/.,") in keep else prompts.UPDATE_LINK_PLACEHOLDER,
                    body)


def body_problem(failure: Failure, body: str) -> str | None:
    """Code checks on the drafted text: length, no other address, no other amount, no promises."""
    if len(body) > MAX_BODY_CHARS:
        return "the draft was too long"
    if any(e.casefold() != failure.customer_email.casefold() for e in _EMAIL.findall(body)):
        return "the draft named an address that isn't the customer's"
    amount = scoring.money(failure.amount_minor, failure.currency).split(" ")[0]
    allowed = {amount, amount.replace(",", "")}
    if any(m not in allowed for m in _MONEY.findall(body)):
        return "the draft named an amount that isn't this failure's"
    if _PROMISES.search(body):
        return "the draft offered or mentioned something it must not (a waiver, discount, refund or fraud)"
    return None


def email_draft(failure: Failure, text: DraftText, tool: str, tier: str, body: str) -> Draft:
    """Recipient comes from the checked failure, never from the draft model."""
    return Draft(number=failure.number, key=failure.key, channel="email", to=failure.customer_email,
                 to_name=failure.customer_name or "", subject=text.subject, body=body, tool=tool, thread_ref="",
                 link=failure.link or "", gist=f"failed payment {failure.invoice_id or failure.id}", tier=tier)


def digest_draft(channel: str, body: str, tool: str) -> Draft:
    name = f"#{channel.lstrip('#')}"
    return Draft(number="", key=DIGEST_KEY, channel="chat", to=name, to_name=name, subject="", body=body, tool=tool,
                 thread_ref="", link="", gist="high-value failed payments digest", tier="digest")


def approval_for(draft: Draft, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this recipient or channel, this subject, this body."""
    return Approval(action="recovery email" if draft.channel == "email" else "digest post",
                    target=f"{draft.who} ({draft.channel})", draft=body or draft.body, tool=draft.tool,
                    recipient=draft.to, subject=draft.subject)


def approval_question(draft: Draft) -> tuple[str, dict]:
    """The pause text (everything that will be sent) and the structured question for the card."""
    if draft.channel == "chat":
        header, yes, hint = f"Post this to {draft.to}?", POST, "Reply Post, Skip, or type your own text to post."
    else:
        name = draft.who if len(draft.who.encode("utf-8")) <= _HEADER_NAME_MAX else "this customer"
        header, yes, hint = (f"Send this recovery email to {name}?", SEND,
                             "Reply Send, Skip, or type your own text to send instead.")
    hitl = question_metadata(header, [(yes, "Send exactly this text."), (SKIP, "Don't send it; move on.")],
                             multi_select=False, allow_custom_input=True)
    return describe(draft, draft.body) + f"\n\n{hint}", hitl


def describe(draft: Draft, body: str) -> str:
    if draft.channel == "chat":
        lines = [f"Digest post to {draft.to}", f"Via: {draft.tool}"]
    else:
        lines = [f"Recovery email for #{draft.number} ({draft.gist})", f"To: {draft.who} <{draft.to}>",
                 f"Via: {draft.tool}", f"Subject: {draft.subject}", f"Tone: {draft.tier}"]
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
