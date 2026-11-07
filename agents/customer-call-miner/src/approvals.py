"""Pure: the approval cards (CRM notes multi-select, one note, the digest) and reading the answers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

from nasiko_hitl import MAX_OPTIONS, question_metadata
from notes import description
from records import NoteDraft
from send_guard import Approval

SEND, SKIP, POST = "Send", "Skip", "Post"
_YES = frozenset({"send", "post", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send it", "post it"})
_NO = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "don't post", "stop", "denied", "deny",
                 "reject", "rejected", "decline", "declined", "none"})
_ALL = frozenset({"all", "select all", "all of them", "add all"})
_LABEL_BYTES = 150

Decision = Literal["send", "skip", "edit"]


@dataclass(frozen=True)
class DigestPlan:
    channel_id: str
    channel_name: str
    text: str
    tool: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Any) -> DigestPlan | None:
        if not isinstance(data, dict) or not data.get("channel_id"):
            return None
        return DigestPlan(**{f.name: str(data.get(f.name) or "") for f in fields(DigestPlan)})


def batches(notes: Sequence[NoteDraft]) -> list[tuple[NoteDraft, ...]]:
    return [tuple(notes[i:i + MAX_OPTIONS]) for i in range(0, len(notes), MAX_OPTIONS)]


def labels(batch: Sequence[NoteDraft]) -> list[str]:
    """Unique, short card labels: the account name, numbered when two accounts share one."""
    out: list[str] = []
    for note in batch:
        base = note.name.encode("utf-8")[:_LABEL_BYTES].decode("utf-8", errors="ignore").strip() or note.company_id
        label, n = base, 2
        while label in out:
            label, n = f"{base} ({n})", n + 1
        out.append(label)
    return out


def notes_question(batch: Sequence[NoteDraft], more: int) -> tuple[str, dict]:
    names = labels(batch)
    hitl = question_metadata("Add these notes to the CRM?",
                             [(label, description(note.text)) for label, note in zip(names, batch, strict=True)],
                             multi_select=True, allow_custom_input=False)
    shown = "\n\n".join(f"**{label}**\n{note.text}" for label, note in zip(names, batch, strict=True))
    tail = f"\n\n{more} more note(s) follow on the next card." if more else ""
    lead = ("These notes are built only from verified customer quotes. Select the accounts to add them to "
            "(select none to skip all).")
    return f"{lead}\n\n{shown}{tail}", hitl


def selected(answer: str, batch: Sequence[NoteDraft]) -> tuple[NoteDraft, ...]:
    """The notes whose labels appear as lines of the answer ("all" selects every one); anything else is
    ignored, and an empty answer selects none."""
    if (answer or "").strip().casefold().rstrip(".!") in _ALL:
        return tuple(batch)
    by_label = {label.casefold(): note for label, note in zip(labels(batch), batch, strict=True)}
    picked = [by_label[line.strip().casefold()] for line in (answer or "").splitlines()
              if line.strip().casefold() in by_label]
    return tuple(dict.fromkeys(picked))


def single_note_question(note: NoteDraft) -> tuple[str, dict]:
    header = f"Add this note to {labels([note])[0]}?"
    hitl = question_metadata(header, [(SEND, "Add exactly this note."), (SKIP, "Don't add it.")],
                             multi_select=False, allow_custom_input=True)
    return f"{note.text}\n\nReply Send, Skip, or type the note text to add instead.", hitl


def digest_question(plan: DigestPlan) -> tuple[str, dict]:
    hitl = question_metadata(f"Post this digest to #{plan.channel_name.lstrip('#')[:120]}?",
                             [(POST, "Post exactly this text."), (SKIP, "Don't post it.")],
                             multi_select=False, allow_custom_input=True)
    text = (f"Digest for #{plan.channel_name.lstrip('#')} (via {plan.tool}):\n\n{plan.text}\n\n"
            "Reply Post, Skip, or type the text to post instead.")
    return text, hitl


def interpret(answer: str) -> tuple[Decision, str]:
    """Send / Skip / edited text. Empty and refusals are Skip: nothing is written by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "send", ""
    return "edit", text


def note_approval(note: NoteDraft, tool: str, assoc_tool: str, text: str | None = None) -> Approval:
    return Approval(action=f"add a note to the CRM company record {note.company_id}", target=note.name,
                    draft=text or note.text, tool=tool, target_id=note.company_id, kind="note",
                    assoc_tool=assoc_tool)


def digest_approval(plan: DigestPlan, text: str | None = None) -> Approval:
    return Approval(action=f"post a message to the channel {plan.channel_id}", target=plan.channel_name,
                    draft=text or plan.text, tool=plan.tool, target_id=plan.channel_id, kind="post")
