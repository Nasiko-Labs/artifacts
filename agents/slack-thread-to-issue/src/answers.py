"""Pure: the approval questions (stock chat RequestCard metadata) and reading the human's answers.
Empty or unclear refusals are Skip: nothing is written by default."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

import issue
from models import IssueDraft, Named
from nasiko_hitl import question_metadata

APPROVE = "Approve"
POST = "Post"
SKIP = "Skip"
MAX_CHOICES = 10
_HEADER_TITLE_MAX = 60
_HEADER_LINK_MAX = 120
_YES = frozenset({"approve", "approved", "yes", "y", "ok", "okay", "create", "create it", "post", "post it",
                  "send", "send it", "go", "go ahead", "authorized"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "don't", "dont", "deny", "denied", "reject", "rejected",
                 "decline", "declined", "don't create", "don't post", "don't send"})
_TITLE_EDIT = re.compile(r"^\s*(?:change\s+(?:the\s+)?)?title\s*(?::|to|=)\s*(.+)$", re.IGNORECASE | re.DOTALL)
_LABEL_EDIT = re.compile(r"^\s*(?:set\s+)?labels?\s*(?::|to|=)\s*(.*)$", re.IGNORECASE | re.DOTALL)
_NO_LABELS = re.compile(r"^\s*(?:no|remove(?: all)?|clear(?: the)?)\s+labels?\s*$", re.IGNORECASE)

Decision = Literal["yes", "skip", "other"]


def decision(answer: str) -> tuple[Decision, str]:
    text = (answer or "").strip()
    word = text.casefold().rstrip(".! ")
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "yes", ""
    return "other", text


def _short(text: str, limit: int) -> str:
    return text if len(text.encode("utf-8")) <= limit else text.encode("utf-8")[:limit].decode("utf-8", "ignore") + "..."


def create_question(draft: IssueDraft) -> dict:
    team = f" in {_short(draft.team.name, 40)}" if draft.team else ""
    header = f"Create issue '{_short(draft.title, _HEADER_TITLE_MAX)}'{team}?"
    return question_metadata(header, [(APPROVE, "Create exactly this issue."), (SKIP, "Don't create it; move on.")],
                             multi_select=False, allow_custom_input=True)


def linkback_question(text: str) -> dict:
    header = f"Post '{_short(text, _HEADER_LINK_MAX)}' in the thread?"
    return question_metadata(header, [(POST, "Post exactly this text as a reply in the thread."),
                                      (SKIP, "Don't post it; move on.")],
                             multi_select=False, allow_custom_input=True)


def choice_question(header: str, labels: Sequence[str]) -> dict:
    return question_metadata(header, [(label, "") for label in labels[:MAX_CHOICES]], multi_select=False,
                             allow_custom_input=False)


def pick(answer: str, options: Sequence[str]) -> str | None:
    """The option the answer names: exact (case-insensitive) first, then a unique partial match."""
    text = (answer or "").strip().casefold()
    exact = [o for o in options if o.casefold() == text]
    if exact:
        return exact[0]
    partial = [o for o in options if text and (text in o.casefold() or o.casefold() in text)]
    return partial[0] if len(partial) == 1 else None


def pick_team(teams: Sequence[Named], hint: str | None) -> tuple[Named | None, bool]:
    """(team, guessed) for a hint: exact name match, else a unique partial match (guessed=True)."""
    if not hint:
        return None, False
    name = pick(hint, [t.name for t in teams])
    team = next((t for t in teams if t.name == name), None)
    return team, team is not None and team.name.casefold() != hint.strip().casefold()


def edit(draft: IssueDraft, instruction: str, labels: Sequence[Named]) -> tuple[IssueDraft | None, str]:
    """An edited issue from a `title: ...` or `labels: a, b` instruction, or (None, why it was not applied)."""
    title = _TITLE_EDIT.match(instruction)
    if title:
        new = " ".join(title.group(1).split())
        if issue.title_problem(new):
            return None, ("That title doesn't fit: it must start with a verb, be at most 80 characters and have "
                          "no trailing period.")
        return replace(draft, title=new), ""
    if _NO_LABELS.match(instruction):
        return replace(draft, labels=()), ""
    wanted = _LABEL_EDIT.match(instruction)
    if wanted:
        names = [n.strip() for n in re.split(r"[,;]", wanted.group(1)) if n.strip()]
        found = [next((x for x in labels if x.name.casefold() == n.casefold()), None) for n in names]
        if any(f is None for f in found) or len(found) > issue.MAX_LABELS:
            known = ", ".join(x.name for x in labels) or "none"
            return None, f"I can only use existing labels (at most {issue.MAX_LABELS}). Known labels: {known}."
        return replace(draft, labels=tuple(found)), ""
    return None, ('I can change the title ("title: Fix the export") or the labels ("labels: bug, billing" or '
                  '"no labels"). Otherwise answer Approve or Skip.')
