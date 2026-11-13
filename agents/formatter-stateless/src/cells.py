"""Pure: the shared text of a document that every renderer shows the same way (scalar spelling, the
footer line, the warning line). Escaping is the caller's job."""

from __future__ import annotations

import json
from typing import Any

from model import Document

WARNING = "⚠ Needs a person to review"
NONE_MARK = "—"


def scalar_text(value: Any) -> str:
    """Booleans as yes/no, null as an em dash, numbers unchanged, strings as given."""
    if value is None:
        return NONE_MARK
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return str(value)


def json_text(value: Any, pretty: bool = False) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2 if pretty else None)


def footer_parts(doc: Document) -> list[str]:
    """['Confidence: medium', 'Needs a person: yes'], only for the keys the input had (as raw text)."""
    if doc.footer is None:
        return []
    confidence, needs_human = doc.footer
    parts = []
    if confidence is not None:
        parts.append(f"Confidence: {scalar_text(confidence)}")
    if needs_human is not None:
        parts.append(f"Needs a person: {scalar_text(needs_human)}")
    return parts


def doc_heading(doc: Document, index: int) -> str:
    """The heading of one envelope among several: its summary, else its position."""
    return doc.lead if doc.lead else f"Envelope {index}"
