"""Pure: "shorter" on the memo the user already saw, read back from the history (this agent keeps no state).
The rendered memo is cut down in code to the tight caps (`memo.TIGHT`): context to its first sentence, one pro
and one con per option, one reason, the top risks and reversal triggers. The scores and the recommendation stay
exactly as shown: no model call, so nothing is re-judged and the result is always the same memo, shorter."""

from __future__ import annotations

import re

from memo import TIGHT, count

MAX_TRIGGERS = 2
ALREADY_SHORT = "_This memo is already at its shortest; say \"just the recommendation\" for one line._"
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_KEPT_NOTES = ("_Open items:", "_Review before sign-off:")


def condense(reply: str) -> tuple[str, bool]:
    """(the memo blocks of `reply` cut to the tight caps, whether that made it shorter). Footer notes are
    dropped except open items and the review flag; the caller adds the follow-up help line."""
    blocks = [b for b in reply.split("\n\n") if b.strip()]
    memo_blocks = [b for b in blocks if not b.startswith("_") or b.startswith("_(j)")]
    kept_notes = [b for b in blocks if b.startswith(_KEPT_NOTES)]
    short = [_block(b) for b in memo_blocks]
    if not any("(j)" in b for b in short if not b.startswith("_(j)")):
        short = [b for b in short if not b.startswith("_(j)")]
    shorter = count("\n\n".join(short)) < count("\n\n".join(memo_blocks))
    return "\n\n".join([*short, *kept_notes]), shorter


def _block(block: str) -> str:
    head, _, body = block.partition("\n")
    if head == "**Context**":
        return f"{head}\n{_SENTENCES.split(body.strip())[0]}"
    if head == "**Options**":
        return f"{head}\n" + "\n".join(_row(line) for line in body.splitlines())
    if head == "**Why**":
        return _first_lines(head, body, TIGHT.rationale)
    if head == "**Risks and mitigations**":
        return _first_lines(head, body, TIGHT.risks)
    if head == "**What would change this decision**":
        return _first_lines(head, body, MAX_TRIGGERS)
    return block


def _first_lines(head: str, body: str, cap: int) -> str:
    return "\n".join([head, *body.splitlines()[:cap]])


def _row(line: str) -> str:
    """An options-table row with one pro and one con (the header and rule rows pass through)."""
    cells = line.strip().strip("|").split(" | ")
    if len(cells) < 3 or line.startswith(("| Option", "|---")):
        return line
    kept = [cells[0].strip(), *(c.split("; ")[0].strip() for c in cells[1:3]), *(c.strip() for c in cells[3:])]
    return "| " + " | ".join(kept) + " |"
