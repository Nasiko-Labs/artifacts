"""The one question this agent pauses on (Q1 "Which repository?", rendered by the chat's request card) and how
an answer is read. Reading fails closed: an answer that names no `owner/repo` is a cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import RepoRef, current_message, repo_in
from nasiko_hitl import question_metadata

METADATA_KEY = "issue_localizer"
NEED_REPO = "need_repo"
HEADER = "Which repository?"
CANCEL = "Cancel"
MAX_REQUEST_CHARS = 20_000

_LOOSE_REPO = re.compile(r"(?<![\w/.-])([A-Za-z0-9][\w-]*)/([A-Za-z0-9][\w-]*)(?:@([\w./-]+))?(?![\w/])")
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    request: str
    options: tuple[str, ...] = ()
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": NEED_REPO, "request": self.request[:MAX_REQUEST_CHARS],
                               "options": list(self.options), "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_REPO or not isinstance(data.get("request"), str):
            return None
        options = tuple(str(o) for o in data.get("options") or [])
        return Pending(data["request"], options, bool(data.get("wants_json")))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    options: Sequence[tuple[str, str]] = [(o, "") for o in pending.options[:4]] + [(CANCEL, "")]
    return prompts.NEED_REPO, question_metadata(HEADER, options, allow_custom_input=True)


def interpret(raw_answer: str) -> RepoRef | None:
    """The repository the answer names, or None for a cancel or anything unreadable."""
    answer = current_message(raw_answer or "").strip()
    if not answer or _CANCEL.match(answer.casefold()):
        return None
    found = repo_in(answer)
    if found is not None:
        return found
    loose = _LOOSE_REPO.search(answer)
    return RepoRef(loose.group(1), loose.group(2), loose.group(3)) if loose else None
