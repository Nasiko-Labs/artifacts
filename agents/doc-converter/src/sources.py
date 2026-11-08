"""Where a file lives, decided in code from the user's words or a link's host, and the user-facing
labels for each place. The only module that holds these words; tools are found by capability phrases
(discovery.py), and a tool's own name or description is matched against the same words."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

Place = Literal["drive", "dropbox"]
PLACES: tuple[Place, ...] = ("drive", "dropbox")

LABEL: dict[str, str] = {"drive": "Google Drive", "dropbox": "Dropbox", "file": "File", "pasted": "Pasted"}
SHORT_LABEL: dict[str, str] = {"drive": "Drive", "dropbox": "Dropbox", "file": "API file", "pasted": "Pasted"}
PASTE_OPTION = "I'll paste it"

_WORDS: dict[Place, re.Pattern] = {
    "drive": re.compile(r"\bgoogle\s+drive\b|\bgdrive\b|\b(?:from|in|on|at|of)\s+(?:my\s+|the\s+|our\s+)?drive\b"
                        r"|\bgoogle\s+(?:docs?|sheets?|slides)\b", re.IGNORECASE),
    "dropbox": re.compile(r"\bdropbox\b", re.IGNORECASE),
}
_HOSTS: dict[Place, tuple[str, ...]] = {
    "drive": ("drive.google.com", "docs.google.com"),
    "dropbox": ("dropbox.com", "dropboxusercontent.com"),
}
# A tool's name or description placing it in one place.
_TOOL_WORDS: dict[Place, tuple[str, ...]] = {"drive": ("drive",), "dropbox": ("dropbox",)}


def place_of_link(link: str) -> Place | None:
    host = (urlparse(link).hostname or "").casefold()
    for place, hosts in _HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return place
    return None


def place_in_words(text: str) -> Place | None:
    """The first place the text names, by position."""
    hits = [(m.start(), place) for place, rx in _WORDS.items() if (m := rx.search(text or ""))]
    return min(hits)[1] if hits else None


def place_of_tool(name: str, description: str) -> Place | None:
    for text in (name.casefold(), description.casefold()):
        found = [p for p, words in _TOOL_WORDS.items() if any(w in text for w in words)]
        if len(found) == 1:
            return found[0]
    return None


def place_from_answer(answer: str) -> Place | None:
    clean = answer.strip().casefold()
    for place in PLACES:
        if clean == LABEL[place].casefold():
            return place
    return place_in_words(answer)
