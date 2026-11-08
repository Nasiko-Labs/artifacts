"""Pure: text that arrives over several messages ("part 1 of 3, more coming" ... "that's all").

The held text is stored as pieces of at most PIECE_CHARS so no single stored value nears the platform's
1 MiB per-value cap; the whole is capped at CAP_CHARS. Pastes are joined with a newline."""

from __future__ import annotations

from dataclasses import dataclass

CAP_CHARS = 600_000
PIECE_CHARS = 400_000
JOINER = "\n"


@dataclass(frozen=True)
class Appended:
    pieces: tuple[str, ...]
    added: int
    over_cap: bool = False
    would_be: int = 0


def to_pieces(text: str) -> list[str]:
    return [text[i:i + PIECE_CHARS] for i in range(0, len(text), PIECE_CHARS)] if text else []


def joined(pieces) -> str:
    return "".join(pieces or ())


def append(pieces, new: str) -> Appended:
    """The held pieces plus `new`; over the cap, nothing is added and `over_cap` is set."""
    held = joined(pieces)
    combined = f"{held}{JOINER}{new}" if held and new else held or new
    if len(combined) > CAP_CHARS:
        return Appended(tuple(pieces or ()), 0, over_cap=True, would_be=len(combined))
    return Appended(tuple(to_pieces(combined)), len(new))


def cap_cut(text: str) -> str:
    """At most CAP_CHARS of `text`, cut back to the last line break in reach."""
    if len(text) <= CAP_CHARS:
        return text
    head = text[:CAP_CHARS]
    cut = head.rfind("\n")
    return head[:cut] if cut > CAP_CHARS // 2 else head
