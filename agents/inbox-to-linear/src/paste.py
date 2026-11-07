"""Pure: one email pasted into the chat (Subject / From / Date lines, then the body) read as a thread, so
a user without an inbox connection still gets one proposal. The paste is the evidence: every quote is
checked against it."""

from __future__ import annotations

import re

from models import Thread

_HEADER = re.compile(r"^\s*(subject|from|date|sent)\s*:\s*(.*)$", re.IGNORECASE)
_ADDRESS = re.compile(r"[^\s<>,;\"']+@[^\s<>,;\"']+")
_NAME = re.compile(r"^\s*\"?([^\"<]+?)\"?\s*<")


def read_paste(text: str) -> Thread | None:
    """A thread from a pasted email, or None when the message is not one (needs Subject and From)."""
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    for line in (text or "").splitlines():
        match = _HEADER.match(line)
        if match and not body_lines and match.group(1).lower() not in headers:
            headers = {**headers, match.group(1).lower(): match.group(2).strip()}
            continue
        if headers:
            body_lines = [*body_lines, line]
    if "subject" not in headers or "from" not in headers:
        return None
    sender = headers["from"]
    address = _ADDRESS.search(sender)
    name = _NAME.match(sender)
    return Thread(id="p1", subject=headers["subject"], sender=address.group(0) if address else sender,
                  sender_name=name.group(1).strip() if name else "",
                  date=headers.get("date") or headers.get("sent") or "", text="\n".join(body_lines).strip())
