"""Pure: what the user sent. The user's words are the FIRST text part only (later parts are platform org
policy / knowledge); text after the last `Current message:` marker; JSON output when asked in words or by
metadata `{"output": "json"}`; an envelope from an earlier run accepted as input."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import envelope

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
_JSON_WORDS = re.compile(r"\b(?:as json|json only|in json|json format)\b", re.IGNORECASE)
_REVIEW_WORDS = re.compile(r"\b(?:reviews?|ratings?|stars?|app ?store|play store|complain\w*|versions?|releases?|"
                           r"competitors?|rival)\b", re.IGNORECASE)


def current_message(raw: str) -> str:
    idx = raw.rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def wants_json(text: str, metadata: Mapping[str, Any] | None = None) -> bool:
    asked = str((metadata or {}).get("output") or "").casefold() == "json"
    return asked or bool(_JSON_WORDS.search(text or ""))


def mentions_reviews(text: str) -> bool:
    return bool(_REVIEW_WORDS.search(text or ""))


def input_envelope(text: str) -> dict | None:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    return dict(data) if envelope.looks_like(data) else None
