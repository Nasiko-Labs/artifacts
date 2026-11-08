"""The one pause: no topic. Q1 asks for it with four kinds as options; picking a kind asks once more, in
words. The answer comes back as text on the same task; what to resume from rides on the task metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_hitl import question_metadata

METADATA_KEY = "opportunity_brief"
NEED_TOPIC, WHICH = "need_topic", "which"
CANCEL = "Cancel"
KINDS = ("A product area", "A technology", "A competitor", "A customer problem")
Q1_HEADER = "What topic should I look at?"
Q1_TEXT = ("What topic should I build the brief on? Type it in a few words (for example \"AI coding agents for "
           "small teams\"), or pick what kind of topic it is.")


@dataclass(frozen=True)
class Pending:
    kind: str                          # need_topic | which
    request: str = ""                  # the original message, re-parsed on resume
    picked: str = ""                   # the option picked at Q1
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "request": self.request, "picked": self.picked,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_TOPIC, WHICH):
            return None
        return Pending(str(data["kind"]), str(data.get("request") or ""), str(data.get("picked") or ""),
                       bool(data.get("wants_json")))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    if pending.kind == WHICH:
        noun = pending.picked.removeprefix("A ").strip() or "topic"
        header = f"Which {noun}? Type it in a few words."
        return header, question_metadata(header, [(CANCEL, "")])
    return Q1_TEXT, question_metadata(Q1_HEADER, [(k, "") for k in KINDS] + [(CANCEL, "")])


def interpret(pending: Pending, answer: str) -> tuple[str, str]:
    """('cancel', '') | ('ask_which', kind) | ('topic', text)."""
    text = " ".join((answer or "").split())
    folded = text.casefold().strip(" .!")
    if not folded or folded in {"cancel", "stop", "never mind", "nevermind", "no", "skip"}:
        return "cancel", ""
    kind = next((k for k in KINDS if k.casefold() == folded), None)
    if kind is not None and pending.kind == NEED_TOPIC:
        return "ask_which", kind
    return "topic", text
