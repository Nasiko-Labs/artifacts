"""Pure: follow-ups on a stored review that need no model call: re-render views ("only the high-risk ones",
"show evidence", "apply order", "only <address>"), "why is <address> risky" (the stored reasons) and the
confirm-intent message."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

MAX_COMMAND_WORDS = 14

_BLOCK = re.compile(r"\bblock(?:ing|ed|-level| level|ers?)?\b", re.IGNORECASE)
_HIGH = re.compile(r"\bhigh(?:[- ]risk)?\b|\bcritical\b|\bbiggest risks?\b", re.IGNORECASE)
_MEDIUM = re.compile(r"\bmedium(?:[- ]risk)?\b", re.IGNORECASE)
_EVIDENCE = re.compile(r"\b(?:evidence|sources?|plan lines?|show (?:me )?the lines?)\b", re.IGNORECASE)
_ORDER = re.compile(r"\b(?:apply order|order of apply|what order|in what order|apply plan|sequence)\b", re.IGNORECASE)
_QUESTIONS = re.compile(r"\b(?:questions?(?: to ask)?|what (?:should|do) i ask)\b", re.IGNORECASE)
_FULL = re.compile(r"^\s*(?:show (?:it |the review )?again|full review|everything|show all|all findings|again)\W*$",
                   re.IGNORECASE)
_JSON_ONLY = re.compile(r"^\s*(?:(?:show it|give it|return it|send it)\s+)?(?:as json|json only|in json)\W*$",
                        re.IGNORECASE)
_WHY = re.compile(r"\bwhy\b|\bexplain\b|\bwhat(?:'s| is) (?:wrong|risky)\b", re.IGNORECASE)
_ONLY = re.compile(r"\b(?:only|just|show|list)\b", re.IGNORECASE)


@dataclass(frozen=True)
class View:
    view: str = "default"     # default | block | high | medium | address | order | questions
    evidence: bool = False
    address: str = ""


def find_address(text: str, addresses: Sequence[str]) -> str:
    """The stored address the text names (full or `type.name` short form); longest match wins."""
    hits = []
    for address in addresses:
        short = address.split(".", address.count(".") - 1)[-1] if address.startswith("module.") else address
        if address in text or (short != address and short in text):
            hits.append(address if address in text else short)
    return max(hits, key=len) if hits else ""


def parse_view(text: str, addresses: Sequence[str]) -> View | None:
    """A re-render command, or None when the message is something else."""
    if len((text or "").split()) > MAX_COMMAND_WORDS:
        return None
    evidence = bool(_EVIDENCE.search(text))
    if _FULL.match(text) or _JSON_ONLY.match(text):
        return View("default", evidence)
    address = find_address(text, addresses)
    if address and (_WHY.search(text) or _ONLY.search(text) or text.strip().strip("`?.") == address):
        return View("address", evidence, address)
    if _ORDER.search(text):
        return View("order")
    if _BLOCK.search(text) and _ONLY.search(text):
        return View("block", evidence)
    if _HIGH.search(text) and not _MEDIUM.search(text):
        return View("high", evidence)
    if _MEDIUM.search(text) and not _HIGH.search(text):
        return View("medium", evidence)
    if _QUESTIONS.search(text) and _ONLY.search(text) or (text or "").strip().casefold() in ("questions", "questions?"):
        return View("questions")
    if evidence:
        return View("default", True)
    return None
