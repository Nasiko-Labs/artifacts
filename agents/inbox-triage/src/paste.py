"""Pure: pasted threads (when no email tool is connected) split in code on `From:` lines, one Mail per block.

Pasted text is untrusted data like tool output: fence markers are stripped and it is verified against the same way."""

from __future__ import annotations

import re

from mailparse import iso, to_datetime
from models import Mail
from prompts import strip_markers

MAX_PASTED_THREADS = 60
_FROM_LINE = re.compile(r"^[ \t]*From:[ \t]*\S", re.IGNORECASE | re.MULTILINE)
_HEADER = re.compile(r"^[ \t]*(From|To|Cc|Subject|Date|List-Unsubscribe|Precedence):[ \t]*(.*)$", re.IGNORECASE)


def is_paste(text: str) -> bool:
    return bool(_FROM_LINE.search(text or ""))


def split(text: str) -> tuple[Mail, ...]:
    clean = strip_markers(text or "")
    starts = [m.start() for m in _FROM_LINE.finditer(clean)]
    blocks = [clean[a:b].strip() for a, b in zip(starts, [*starts[1:], len(clean)], strict=True)]
    return tuple(_mail(i, block) for i, block in enumerate(blocks[:MAX_PASTED_THREADS], start=1))


def count(text: str) -> int:
    return len(_FROM_LINE.findall(strip_markers(text or "")))


def _mail(number: int, block: str) -> Mail:
    headers: dict[str, str] = {}
    lines = block.splitlines()
    body_start = 0
    while body_start < len(lines) and (match := _HEADER.match(lines[body_start])):
        headers.setdefault(match.group(1).casefold(), match.group(2).strip())
        body_start += 1
    body = "\n".join(lines[body_start:]).strip()
    subject = headers.get("subject", "")
    return Mail(
        id=f"p{number}", sender=headers.get("from", ""), subject=subject,
        date=iso(to_datetime(headers.get("date"))), to=headers.get("to", ""), cc=headers.get("cc", ""),
        last_text=body, text=f"{subject}\n{body}", list_unsubscribe=bool(headers.get("list-unsubscribe")),
        precedence_bulk=headers.get("precedence", "").casefold() in ("bulk", "list", "junk"),
    )
