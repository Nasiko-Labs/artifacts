"""Pure: speaker names. `norm_name` is for matching only (casefold, trim); display keeps the written form.
`is_name` decides whether a header's label is a speaker or a label word like "Update"."""

from __future__ import annotations

MAX_NAME_WORDS = 4
MAX_NAME_CHARS = 40
NOT_NAMES = frozenset({"update", "note", "notes", "decision", "decisions", "action", "actions", "action item",
                       "action items", "todo", "to do", "fyi", "ps", "p.s", "re", "subject", "date", "sent", "to",
                       "cc", "bcc", "from", "edit", "edited", "important", "question", "questions", "answer",
                       "summary", "agenda", "eta", "tl;dr", "tldr", "next steps", "status", "reminder", "http",
                       "https", "example", "e.g", "i.e", "also", "and", "but", "so", "ok", "okay", "yes", "no"})


def norm_name(name: str) -> str:
    return " ".join((name or "").replace("’", "'").casefold().lstrip("@").split()).strip(" .,:;")


def is_name(name: str) -> bool:
    """A speaker label: 1-4 words, not a label word like "Update"; a multi-word name is capitalised."""
    plain = norm_name(name)
    words = name.split()
    capitalised = len(words) == 1 or all(w[0].isupper() or w[0] == "@" for w in words)
    return (0 < len(words) <= MAX_NAME_WORDS and len(plain) <= MAX_NAME_CHARS and plain not in NOT_NAMES
            and capitalised and not plain.startswith(("http", "www")))
