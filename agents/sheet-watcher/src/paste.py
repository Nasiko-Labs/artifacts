"""Pure: a table the user pasted, or a previous workflow step's envelope (its `table` field). Never requires
either: with a spreadsheet connector the sheet is read instead."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import table as tables

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def read(text: str) -> tables.Table | None:
    envelope = _envelope(text)
    if envelope is not None and envelope.get("table") is not None:
        found = tables.from_envelope(envelope["table"])
        if found is not None:
            return found
    body = "\n".join(text.splitlines()[1:]) if _has_lead_line(text) else text
    return tables.from_text(body)


def _has_lead_line(text: str) -> bool:
    """A request line before the pasted table ("compare this:") is not a table row."""
    first = (text or "").split("\n", 1)[0]
    return "\n" in (text or "") and "\t" not in first and first.count("|") < 2 and first.count(",") < 2


def _envelope(text: str) -> Mapping[str, Any] | None:
    body = (text or "").strip()
    fenced = _FENCED_JSON.search(body)
    candidate = fenced.group(1) if fenced else body
    if not candidate.startswith("{"):
        return None
    try:
        data = json.loads(candidate)
    except ValueError:
        return None
    return data if isinstance(data, Mapping) else None
