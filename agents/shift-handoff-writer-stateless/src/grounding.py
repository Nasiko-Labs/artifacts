"""Pure: is a quote or a paraphrase backed by the notes? Snippets must appear (normalised) in the cited
entry; numbers, refs, times and names in model-written text must appear in the notes."""

from __future__ import annotations

import re

MIN_SNIPPET_CHARS = 12
SNIPPET_MAX_CHARS = 200

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         "…": "..."})
_SPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:[:.,]\d+)*")
_NAME = re.compile(r"\b[A-Z][a-zA-Z'\-]+\b")
_REF = re.compile(r"#\d{2,}|\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")
_SENTENCE_START = re.compile(r"(?:\A|[.;:!?]\s+)\W*\Z")  # a capital after this is a sentence start, not a name
COMMON_CAPITALS = frozenset({
    "The", "A", "An", "And", "Or", "But", "If", "When", "Do", "Don't", "Not", "No", "Yes", "Check", "Call", "Ask",
    "Waiting", "Customer", "Ticket", "Issue", "Fix", "Fixed", "Resolved", "Open", "Escalated", "Follow", "Send",
    "Still", "Needs", "Need", "Pending", "Blocked", "Vendor", "Promise", "Told", "Two", "Three", "Four", "Five",
    "One", "Six", "Seven", "Eight", "Nine", "Ten", "Several", "Many", "Hot", "Urgent", "Today", "Tomorrow",
    "Tonight", "Next", "Morning", "Evening", "Night", "Before", "After", "Until", "Overnight", "SLA", "EOD", "ASAP",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Reply", "Update", "Export",
    "Login", "Password", "Reset", "Refund", "Billing", "Outage", "Bug", "Enterprise", "Team", "Eng", "Support",
})


def normalise(text: str) -> str:
    return _SPACE.sub(" ", (text or "").translate(_QUOTES).casefold()).strip()


def contains(haystack: str, needle: str) -> bool:
    small = normalise(needle).strip(" .,;:\"'")
    return len(small) >= MIN_SNIPPET_CHARS and small in normalise(haystack)


def present(notes_norm: str, value: str | None) -> bool:
    """A short as-written value (owner, due, customer, ref) appears in the notes."""
    small = normalise(value or "").strip(" .,;:\"'")
    return bool(small) and small in notes_norm


def ungrounded(text: str | None, notes_norm: str, allowed: frozenset[str] = frozenset()) -> list[str]:
    """Numbers, refs and capitalised names in `text` that the notes do not contain."""
    if not text:
        return []
    missing = []
    tokens = [*_NUMBER.findall(text), *_REF.findall(text)]
    tokens += [m.group(0) for m in _NAME.finditer(text)
               if m.group(0) not in COMMON_CAPITALS and not _SENTENCE_START.search(text[:m.start()])]
    for token in tokens:
        if token in allowed or normalise(token) in notes_norm:
            continue
        missing.append(token)
    return missing


def clip(text: str, limit: int = SNIPPET_MAX_CHARS) -> str:
    text = _SPACE.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
