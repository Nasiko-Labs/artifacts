"""The one question this agent may pause on: "Paste the error", when a message asks for help with an error but
holds no error text. The answer comes back on the same task; reading it fails closed (Cancel stops)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "stack_trace_explainer"
NEED_TRACE = "need_trace"
HEADER = "Paste the error"
CANCEL = "Cancel"
REQUEST_MAX_CHARS = 500

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\W*$")


@dataclass(frozen=True)
class Pending:
    kind: str
    request: str = ""          # the user's original words, kept as the request for the resumed turn
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "request": self.request, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_TRACE:
            return None
        return Pending(NEED_TRACE, str(data.get("request") or "")[:REQUEST_MAX_CHARS], bool(data.get("wants_json")))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    return prompts.ASK_TRACE_TEXT, question_metadata(HEADER, [(CANCEL, "")], allow_custom_input=True)


def answer_text(raw: str) -> str | None:
    """The pasted error, or None when the user cancelled (or sent nothing)."""
    text = current_message(raw or "").strip()
    if not text or _CANCEL.match(" ".join(text.casefold().split())):
        return None
    return text
