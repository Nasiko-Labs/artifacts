"""Every pause this agent makes (one question each, rendered by the chat's request card) and how an answer
is read. Kinds: Q1 "Which repository?", Q2 "Which branch should the PR target?", Q3 "Which file should I
change?", the closed-issue check, the approval cards (one per write step) and the "Show the diff only?"
card when no write tool is granted. Reading fails closed: anything unreadable is a stop, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "issue_to_pr"
KINDS = ("need_repo", "base_branch", "pick_file", "closed", "approval", "diff_only")
MAX_REQUEST_CHARS = 20_000
MAX_LABEL_BYTES = 200
APPROVE, SKIP, STOP, CONTINUE, SHOW_DIFF, CANCEL = "Approve", "Skip", "Stop", "Continue", "Show diff", "Cancel"

_STOP = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip|don'?t)\b")
_YES = re.compile(r"^(?:approve|approved|yes|y|ok|okay|go|go ahead|continue|do it|lgtm|ship it)\b[.!]*$")


@dataclass(frozen=True)
class Pending:
    kind: str
    request: str = ""
    options: tuple[str, ...] = ()
    wants_json: bool = False
    step: int = -1
    digest: str = ""
    header: str = ""
    text: str = ""            # shown with the card; not kept on the task (it can hold a whole diff)

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "request": self.request[:MAX_REQUEST_CHARS],
                               "options": list(self.options), "wants_json": self.wants_json, "step": self.step,
                               "digest": self.digest, "header": self.header}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in KINDS:
            return None
        step = data.get("step")
        return Pending(str(data["kind"]), str(data.get("request") or ""),
                       tuple(str(o) for o in data.get("options") or []), bool(data.get("wants_json")),
                       int(step) if isinstance(step, (int, float)) else -1, str(data.get("digest") or ""),
                       str(data.get("header") or ""))


def label(text: str) -> str:
    """An option label within the platform's byte limit."""
    raw = text.strip()
    while len(raw.encode("utf-8")) > MAX_LABEL_BYTES:
        raw = raw[:-2] + "…"
    return raw


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    options: Sequence[tuple[str, str]] = [(label(o), "") for o in pending.options]
    return pending.text, question_metadata(label(pending.header), options, allow_custom_input=True)


def answer_text(raw: str) -> str:
    return current_message(raw or "").strip()


def is_stop(answer: str) -> bool:
    return not answer or bool(_STOP.match(answer.casefold()))


def is_yes(answer: str) -> bool:
    return bool(_YES.match(answer.casefold()))
