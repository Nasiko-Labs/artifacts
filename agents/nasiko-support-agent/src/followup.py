"""The troubleshooting conversation: at most two clarifying turns per A2A contextId, then a best-guess
answer. The state is a small immutable record per context, kept in a bounded in-memory map (the oldest
contexts are dropped first)."""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

MAX_CLARIFY_TURNS = 2
MAX_CONTEXTS = 2_000

BUILD = "build"
STOPPED = "stopped"
CHAT = "chat"

_CHOICES = (
    (BUILD, re.compile(r"^\s*(?:1|one|first)\b|\b(?:build|upload|zip)\w*\b.*\bfail|\bfail\w*\b.*\b(?:build|upload)",
                       re.IGNORECASE)),
    (STOPPED, re.compile(r"^\s*(?:2|two|second)\b|\b(?:stopped|crash\w*|failed|won'?t start|not running|"
                         r"keeps restarting|exited)\b", re.IGNORECASE)),
    (CHAT, re.compile(r"^\s*(?:3|three|third)\b|\b(?:chat|reply|replies|respond\w*|answer\w*|running)\b",
                      re.IGNORECASE)),
)
_LOGISH = re.compile(r"\n.*\n|\b(?:error|exception|traceback|warn|info|status)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Pending:
    asked: int          # clarifying questions asked so far (1 or 2)
    symptom: str = ""   # build | stopped | chat | "" (not known yet)
    guessed: bool = False  # the best guess was given; a short follow-up gets the hand-off line


def symptom_of(text: str) -> str:
    for name, pattern in _CHOICES:
        if pattern.search(text or ""):
            return name
    return ""


def is_answer(text: str, looks_like_question: bool) -> bool:
    """Does this message continue the troubleshooting (a choice, or pasted output) rather than start over?"""
    if symptom_of(text) or _LOGISH.search(text or ""):
        return True
    return not looks_like_question


class Store:
    """contextId → Pending. Thread-safe; bounded."""

    def __init__(self, max_contexts: int = MAX_CONTEXTS):
        self._max = max_contexts
        self._items: OrderedDict[str, Pending] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, context_id: str) -> Pending | None:
        with self._lock:
            return self._items.get(context_id)

    def put(self, context_id: str, pending: Pending) -> None:
        with self._lock:
            self._items[context_id] = pending
            self._items.move_to_end(context_id)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def clear(self, context_id: str) -> None:
        with self._lock:
            self._items.pop(context_id, None)
