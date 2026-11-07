"""Pure: the approval card for one nudge, how an answer is read, and the approval the send guard binds.

Options: `Post nudge`, `Post nudge and mention @<login>` (only when someone is awaited), `Skip`; typed
text replaces the body, with every @ stripped (a mention happens only through its own option)."""

from __future__ import annotations

import re

from nasiko_hitl import question_metadata
from nudge import Proposal
from send_guard import Approval

POST, MENTION, SKIP = "Post nudge", "Post nudge and mention", "Skip"
_MENTION = re.compile(r"@(?=[A-Za-z0-9])")
_YES = frozenset({"post", "post nudge", "post it", "yes", "y", "ok", "okay", "approve", "approved", "send",
                  "do it", "authorized"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "deny", "denied", "reject", "rejected", "decline", "declined",
                 "don't", "dont"})


def mention_label(p: Proposal) -> str:
    return f"{MENTION} @{p.mention}"


def question(p: Proposal, position: str = "") -> tuple[str, dict]:
    """The pause text (everything that will be written) and the structured question for the card."""
    options = [(POST, "Post exactly this text.")]
    if p.mention:
        options.append((mention_label(p), f"Post it with a cc line that notifies @{p.mention}."))
    options.append((SKIP, "Don't post anything."))
    hitl = question_metadata(f"Comment on {p.destination}?"[:200], options, allow_custom_input=True)
    lead = f"Nudge {position}".strip() if position else "Nudge"
    text = (f"{lead}: {describe(p, p.body)}\n\nReply with an option, or type your own text to post instead "
            "(any @ is removed; mention someone only with the mention option).")
    return text, hitl


def describe(p: Proposal, body: str) -> str:
    return "\n".join([f"Destination: {p.destination} (\"{p.pr_title[:80]}\")", f"Via: {p.tool}", "", body])


def strip_mentions(text: str) -> str:
    return _MENTION.sub("", text)


def interpret(answer: str, p: Proposal) -> tuple[str, str]:
    """(decision, body): skip / post / mention / edit. Empty and unclear refusals are skip."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if p.mention and word == mention_label(p).casefold():
        return "mention", f"{p.body}\n\ncc @{p.mention}"
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "post", p.body
    return "edit", strip_mentions(text)


def approval_for(p: Proposal, body: str) -> Approval:
    return Approval(action="comment", target=p.destination, draft=body, tool=p.tool, owner=p.owner, repo=p.repo,
                    number=p.number, bound=True)
