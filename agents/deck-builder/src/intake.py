"""Pure: deciding what a message is before any call: a command on the existing deck, a missing-content
question, or a deck request/edit. Also the content question (Q1) and its answer."""

from __future__ import annotations

import re

import inputs
from nasiko_hitl import question_metadata

CONTENT_HEADER = "What is the deck about?"
CONTENT_OPTIONS = ("A project update", "A pitch", "A training or how-to", "An all-hands or announcement")
CONTENT_LEAD = ("What should the deck cover? Pick a kind, or better, paste the topic and your key points (or notes, "
                "an outline, a table) and I'll build it.")
_DECK_WORDS = re.compile(r"\b(?:deck|slides?|presentation|powerpoint|ppt|pptx)\b", re.IGNORECASE)
_FILLER = frozenset(["make", "build", "create", "generate", "me", "a", "an", "the", "deck", "slides", "slide", "presentation", "powerpoint", "ppt", "pptx", "please", "for", "from", "about", "on", "of", "my", "our", "some", "can", "you", "i", "need", "want", "new", "with", "could", "would", "like", "to", "do", "quick", "short", "long", "build", "me", "it", "this", "that"])
_JSON_ONLY = re.compile(r"^\W*(?:as json|json|the json|give me the json|show (?:it )?as json)\W*$", re.IGNORECASE)


def content_missing(parsed: inputs.Parsed) -> bool:
    if parsed.notes or parsed.tables or not _DECK_WORDS.search(parsed.request):
        return False
    words = [w for w in re.findall(r"[a-z']+", parsed.request.casefold()) if w not in _FILLER]
    return len(words) < 2


def content_question() -> tuple[str, dict]:
    return CONTENT_LEAD, question_metadata(CONTENT_HEADER, [(o, "") for o in CONTENT_OPTIONS])


def content_answer(original: str, answer: str) -> str:
    """The request to build after Q1: the original request plus the answer (label or free text)."""
    text = answer.strip()
    for option in CONTENT_OPTIONS:
        if text.casefold() == option.casefold():
            return f"{original.strip()} about {option.casefold()}"
    return f"{original.strip()}\n{text}"


def is_json_only(text: str) -> bool:
    return bool(_JSON_ONLY.match(text))


def is_rename_or_folder_only(text: str) -> bool:
    """"call it Q3 board update", "put it in the Board folder": no content edit."""
    if not (inputs.file_name(text) or inputs.folder_name(text)):
        return False
    rest = re.sub(r"\b(?:call|name)\s+it\s+.*", "", text, flags=re.IGNORECASE)
    rest = re.sub(r"\b(?:put|save|upload)\b.*\bfolder\b", "", rest, flags=re.IGNORECASE)
    return not re.findall(r"[a-z]{3,}", rest.casefold())
