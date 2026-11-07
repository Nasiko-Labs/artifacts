"""Pure: the three questions this agent may pause on (one per pause) and reading the answers.

Reading fails closed: an unrecognised answer to "add or start new?" or "over the size limit" is
Cancel (nothing changes); an answer to "paste the outputs" is the source only when it is at least
MIN_SOURCE_WORDS words, otherwise the request ends."""

from __future__ import annotations

import re

from inputs import MIN_SOURCE_WORDS, words
from nasiko_hitl import question_metadata

NOTHING_PASTED = "nothing_pasted"
ADD_OR_NEW = "add_or_new"
OVERSIZE = "oversize"
KINDS = frozenset({NOTHING_PASTED, ADD_OR_NEW, OVERSIZE})

CANCEL = "Cancel"
ADD = "Add to the earlier material"
NEW = "Start a new brief"
USE_FIRST = "Use the first sections that fit"

_HEADERS = {
    NOTHING_PASTED: "Paste the agent outputs you want in the brief",
    ADD_OR_NEW: "Add this to the earlier material or start a new brief?",
    OVERSIZE: "That's more than I can brief in one pass (~12k words). What should I do?",
}
_OPTIONS = {
    NOTHING_PASTED: ((CANCEL, "Stop here; nothing is briefed."),),
    ADD_OR_NEW: ((ADD, "Brief the earlier sections and this paste together."),
                 (NEW, "Forget the earlier sections and brief only this paste."),
                 (CANCEL, "Change nothing.")),
    OVERSIZE: ((USE_FIRST, "Brief the sections in order until the size limit; the rest is left out."),
               (CANCEL, "Change nothing.")),
}
_QUESTIONS = {
    NOTHING_PASTED: "Paste the agent outputs or notes you want in the brief (separate them with --- or headings).",
    ADD_OR_NEW: "You already gave me material in this conversation. Should I add this paste to it, or start a new "
                "brief from this paste only?",
    OVERSIZE: "That's more than I can brief in one pass (about 72,000 characters, ~12k words). Should I brief "
              "the first sections that fit, or cancel?",
}
_WORD = re.compile(r"[^a-z ]+")
_ADD_WORDS = frozenset({"add", "add it", "add to the earlier material", "add to earlier", "append", "add this",
                        "both", "combine"})
_NEW_WORDS = frozenset({"start a new brief", "new", "new brief", "start new", "start over", "start fresh", "fresh",
                        "replace"})
_USE_WORDS = frozenset({"use the first sections that fit", "use first", "use the first", "yes", "ok", "okay",
                        "go ahead", "trim", "use what fits"})


def question(kind: str) -> tuple[str, dict]:
    """(the question text, the structured `status.message.metadata` for the platform's card)."""
    return _QUESTIONS[kind], question_metadata(_HEADERS[kind], _OPTIONS[kind], allow_custom_input=True)


def _norm(text: str) -> str:
    return " ".join(_WORD.sub(" ", (text or "").casefold()).split())


def read_answer(kind: str, text: str) -> str:
    """NOTHING_PASTED → "paste" | "cancel"; ADD_OR_NEW → "add" | "new" | "cancel"; OVERSIZE → "use" | "cancel"."""
    norm = _norm(text)
    if kind == NOTHING_PASTED:
        return "paste" if words(text) >= MIN_SOURCE_WORDS else "cancel"
    if kind == ADD_OR_NEW:
        if norm in _ADD_WORDS:
            return "add"
        return "new" if norm in _NEW_WORDS else "cancel"
    if kind == OVERSIZE:
        return "use" if norm in _USE_WORDS else "cancel"
    return "cancel"
