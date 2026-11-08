"""Pure: read the file out of a storage tool's download/export result. Three shapes are accepted:
1. base64 bytes under a content-like key (size checked before decoding);
2. plain text (a native document exported as text);
3. a download link under a link-like key, fetched later by agent code only if it appears verbatim in
   this turn's tool output."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

MAX_BYTES = 20 * 1024 * 1024
_B64_KEYS = ("file_content_base64", "content_base64", "base64", "bytes", "file_content", "content", "data")
_URL_KEYS = ("s3url", "download_url", "downloadurl", "url", "link")
_TEXT_KEYS = ("content", "text", "body", "file_content", "data")
_MIME_KEYS = ("mimetype", "mime_type", "mime", "content_type")
_B64 = re.compile(r"^[A-Za-z0-9+/\r\n]+={0,2}$")
_BINARY_MAGIC = (b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0")


class TooLarge(ValueError):
    pass


@dataclass(frozen=True)
class Payload:
    data: bytes | None = None
    text: str | None = None
    url: str | None = None
    mime: str | None = None


def read_result(raw_text: str) -> Payload | None:
    """The file in a tool result, or None when the result holds no file content."""
    try:
        body = json.loads(raw_text)
    except ValueError:
        return Payload(text=raw_text) if raw_text.strip() else None
    dicts = list(_dicts(body))
    mime = next((str(d[k]) for d in dicts for k in d if k.casefold() in _MIME_KEYS and isinstance(d[k], str)), None)
    for key in _B64_KEYS:
        for d in dicts:
            data = _decode(_value(d, key), strict=key not in ("content", "data"))
            if data is not None:
                return Payload(data=data, mime=mime)
    for key in _URL_KEYS:
        for d in dicts:
            value = _value(d, key)
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                return Payload(url=value, mime=mime)
    for key in _TEXT_KEYS:
        for d in dicts:
            value = _value(d, key)
            if isinstance(value, str) and value.strip():
                return Payload(text=value, mime=mime)
    return None


def _dicts(node: Any) -> Iterator[dict]:
    """Every dict in the result, breadth first."""
    queue = [node]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            yield current
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)


def _value(d: dict, key: str) -> Any:
    return next((v for k, v in d.items() if k.casefold() == key), None)


def _decode(value: Any, strict: bool) -> bytes | None:
    if not isinstance(value, str) or len(value) < 8 or not _B64.match(value.strip()):
        return None
    if len(value) * 3 // 4 > MAX_BYTES:
        raise TooLarge("the file is larger than 20 MB")
    try:
        data = base64.b64decode(value.strip(), validate=False)
    except (binascii.Error, ValueError):
        return None
    if strict or data.startswith(_BINARY_MAGIC) or _looks_encoded(value.strip(), data):
        return data
    return None


def _looks_encoded(value: str, data: bytes) -> bool:
    """A bare content string that is base64 of text: long, no spaces, padded, decodes to UTF-8."""
    if len(value) < 16 or len(value) % 4 or any(c.isspace() for c in value):
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True
