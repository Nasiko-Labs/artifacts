"""Pure: evidence the user pasted (when no source is connected), one line per item:

    name | kind | detail | date | link (optional)

Read in code, never by the model. The topic is the message's other text. Pasted evidence is fenced
from instructions by construction (only these five fields are kept) and is never checked against a
source, so a pasted answer is always `confidence: low`."""

from __future__ import annotations

import re
from dataclasses import dataclass

from models import KINDS, Evidence, Who
from verify import parse_day

MAX_ROWS = 200
_ALIASES = {"commits": "commit", "pull request": "pr", "prs": "pr", "reviews": "review", "approved": "review",
            "answered": "answer", "answers": "answer", "doc": "doc_author", "wrote": "doc_author",
            "author": "doc_author", "edit": "doc_edit", "edited": "doc_edit"}
_EMAIL = re.compile(r"<?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)>?")


@dataclass(frozen=True)
class Pasted:
    topic: str
    evidence: tuple[Evidence, ...]
    skipped: int = 0


def find(text: str) -> Pasted | None:
    """The pasted rows, or None when the message holds no evidence row."""
    rows, other, skipped = [], [], 0
    for line in (text or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or _kind(cells[1]) is None:
            if line.strip():
                other.append(line.strip())
            continue
        item = _row(cells)
        if item is None:
            skipped += 1
        else:
            rows.append(item)
    if not rows:
        return None
    return Pasted(" ".join(other).strip(), tuple(rows[:MAX_ROWS]), skipped + max(len(rows) - MAX_ROWS, 0))


def _kind(cell: str) -> str | None:
    word = cell.casefold().strip()
    kind = _ALIASES.get(word, word)
    return kind if kind in KINDS else None


def _row(cells: list[str]) -> Evidence | None:
    name, kind, detail, raw_day = cells[0], _kind(cells[1]), cells[2], cells[3]
    link = cells[4] if len(cells) > 4 and cells[4].startswith(("http://", "https://")) else ""
    day = parse_day(raw_day)
    email_match = _EMAIL.search(name)
    email = email_match.group(1) if email_match else ""
    clean_name = _EMAIL.sub("", name).strip(" ,") if email else name
    if kind is None or day is None or not (clean_name or email) or not detail:
        return None
    words = detail.split()
    channel = words[-1].lstrip("#") if kind == "answer" and len(words) > 1 and words[-1].startswith("#") else ""
    question = " ".join(words[:-1]) if channel else detail
    return Evidence(kind, Who(clean_name, email, ""), question, day, link, channel=channel)
