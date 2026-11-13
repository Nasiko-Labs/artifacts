"""Pure: the size cap on one text, and the pieces a recalled source is handed to followup.py in.

Stateless: nothing is held between messages, so this only caps one message's text at CAP_CHARS (cut back
to a line break) and keeps the old piece layout (PIECE_CHARS) that followup.py reads."""

from __future__ import annotations

CAP_CHARS = 600_000
PIECE_CHARS = 400_000


def to_pieces(text: str) -> list[str]:
    return [text[i:i + PIECE_CHARS] for i in range(0, len(text), PIECE_CHARS)] if text else []


def joined(pieces) -> str:
    return "".join(pieces or ())


def cap_cut(text: str) -> str:
    """At most CAP_CHARS of `text`, cut back to the last line break in reach."""
    if len(text) <= CAP_CHARS:
        return text
    head = text[:CAP_CHARS]
    cut = head.rfind("\n")
    return head[:cut] if cut > CAP_CHARS // 2 else head
