"""The one question this agent may pause on, "What should I audit?", and how an answer is read. One question
per pause; reading fails closed (an unrecognised answer never fetches anything)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from inputs import current_message, looks_like_html
from nasiko_hitl import question_metadata

METADATA_KEY = "seo_audit"
WHAT, PASTE, URL = "what", "paste_html", "give_url"
PASTE_LABEL = "I'll paste the page's HTML"
URL_LABEL = "A URL"
CANCEL = "Cancel"
MAX_ATTEMPTS = 2
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_URL = re.compile(r"""\b\w+://[^\s<>"'`]+""")
_BARE_DOMAIN = re.compile(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/\S*)?$", re.IGNORECASE)


@dataclass(frozen=True)
class Pending:
    kind: str = WHAT
    keyword: str = ""
    instruction: str = ""
    attempts: int = 0

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "keyword": self.keyword, "instruction": self.instruction,
                               "attempts": self.attempts}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (WHAT, PASTE, URL):
            return None
        return Pending(kind=data["kind"], keyword=str(data.get("keyword") or ""),
                       instruction=str(data.get("instruction") or ""), attempts=int(data.get("attempts") or 0))


@dataclass(frozen=True)
class Answer:
    action: str          # page (text carries HTML or a URL) | ask (pending) | cancel
    text: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    if pending.kind == WHAT:
        header = "What should I audit?"
        text = "What should I audit? Paste the page's HTML (View Source, select all, copy) or give one URL."
        options = [(PASTE_LABEL, "Works without any network access"), (URL_LABEL, "I fetch one public page")]
    elif pending.kind == PASTE:
        header = "Paste the page's HTML"
        text = "Paste the page's HTML as your answer (View Source, select all, copy)."
        options = [(CANCEL, "")]
    else:
        header = "Which URL should I audit?"
        text = "Type the page's URL (http or https) as your answer."
        options = [(CANCEL, "")]
    if pending.attempts:
        text = "I didn't find a page in that. " + text
    return text, question_metadata(header, options)


def interpret(pending: Pending, raw: str) -> Answer:
    answer = current_message(raw or "").strip()
    plain = " ".join(answer.casefold().split())
    if looks_like_html(answer) or _URL.search(answer):
        return Answer("page", answer)
    if pending.kind in (WHAT, URL) and _BARE_DOMAIN.match(answer):
        return Answer("page", "https://" + answer)
    if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    if pending.kind == WHAT and plain == PASTE_LABEL.casefold():
        return Answer("ask", pending=Pending(PASTE, pending.keyword, pending.instruction))
    if pending.kind == WHAT and plain == URL_LABEL.casefold():
        return Answer("ask", pending=Pending(URL, pending.keyword, pending.instruction))
    if pending.attempts + 1 >= MAX_ATTEMPTS:
        return Answer("cancel")
    return Answer("ask", pending=Pending(pending.kind, pending.keyword, pending.instruction, pending.attempts + 1))
