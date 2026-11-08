"""Pure: what the user gave this turn.

- The user's own words are the FIRST text part. Later text parts are org policy / agent knowledge: context
  for the model, never the page to audit.
- With chat history the text is "{history}\\n\\nCurrent message: {text}"; only the text after the last marker
  is used (earlier audits come from the checkpointer).
- A page is pasted HTML (an `<html`, `<head` or doctype marker, or at least 3 tags) or one http(s) URL. Both
  present: the paste wins and the URL is only the page's address for canonical checks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_HTML_CHARS = 100_000
KEYWORD_MAX_CHARS = 80
MIN_TAGS_FOR_HTML = 3

_HTML_MARKER = re.compile(r"<!doctype\s+html|<html[\s>]|<head[\s>]", re.IGNORECASE)
_TAG = re.compile(r"<[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?>")
_URL = re.compile(r"""\b(?:https?|ftp|file)://[^\s<>"'`)\]]+""", re.IGNORECASE)
_KEYWORD = (
    re.compile(r"""\bkeyword[s]?\s*[:=]?\s*["'“‘]([^"'”’\n]{2,80})["'”’]""", re.IGNORECASE),
    re.compile(r"""\b(?:for|target(?:ing)?)\s+(?:the\s+)?(?:keyword|query|topic)?\s*["'“‘]([^"'”’\n]{2,80})["'”’]""",
               re.IGNORECASE),
    re.compile(r"\bkeyword[s]?\s*[:=]\s*([^\n.,;<]{2,80})", re.IGNORECASE),
)
_JSON_MODE = re.compile(r"\b(?:as json|json only|in json|json output)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    metadata_json: bool = False


@dataclass(frozen=True)
class Request:
    """What a message asks for, read in code (no model)."""

    html: str = ""
    url: str = ""
    keyword: str = ""
    instruction: str = ""      # the text outside the pasted HTML
    json_mode: bool = False
    truncated: bool = False
    envelope: dict | None = None


def current_message(raw: str) -> str:
    idx = raw.rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_parts(parts: Any, metadata: Any = None) -> Incoming:
    texts = [p.text for p in parts or () if p.WhichOneof("content") == "text"]
    user = current_message(texts[0]) if texts else ""
    wants_json = isinstance(metadata, dict) and str(metadata.get("output") or "").casefold() == "json"
    return Incoming(text=user, context=tuple(t for t in texts[1:] if t.strip()), metadata_json=wants_json)


def looks_like_html(text: str) -> bool:
    return bool(_HTML_MARKER.search(text or "")) or len(_TAG.findall(text or "")) >= MIN_TAGS_FOR_HTML


def html_span(text: str) -> tuple[int, int] | None:
    """Where the pasted HTML starts and ends in the message."""
    if not looks_like_html(text):
        return None
    marker = _HTML_MARKER.search(text)
    start = marker.start() if marker else _TAG.search(text).start()
    end = text.rfind(">") + 1
    return (start, end) if end > start else None


def parse_request(incoming: Incoming) -> Request:
    text = incoming.text or ""
    json_mode = incoming.metadata_json or bool(_JSON_MODE.search(text[:500]))
    envelope = read_envelope(text)
    if envelope is not None:
        return Request(instruction=text[:300], json_mode=json_mode, envelope=envelope)
    span = html_span(text)
    html, outside = "", text
    if span is not None:
        html = text[span[0]:span[1]]
        outside = (text[:span[0]] + " " + text[span[1]:]).strip()
    truncated = len(html) > MAX_HTML_CHARS
    urls = _URL.findall(outside)
    return Request(html=html[:MAX_HTML_CHARS], url=urls[0].rstrip(".,;:!?") if urls else "",
                   keyword=find_keyword(outside), instruction=outside[:500], json_mode=json_mode, truncated=truncated)


def find_keyword(text: str) -> str:
    for pattern in _KEYWORD:
        match = pattern.search(text or "")
        if match:
            return " ".join(match.group(1).split())[:KEYWORD_MAX_CHARS].strip()
    return ""


def read_envelope(text: str) -> dict | None:
    """A previous audit's envelope (JSON, optionally fenced), for an "audit again" comparison."""
    body = (text or "").strip()
    if "```" in body:
        inner = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.DOTALL)
        body = inner.group(1) if inner else body
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("items"), list) and "score" in data:
        return data
    return None
