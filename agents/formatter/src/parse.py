"""Pure: finds the JSON in a message. Fenced ```json blocks first (one envelope each); else every
balanced top-level `{...}` / `[...]` in the text that parses as JSON. A JSON array of envelopes is
several envelopes. Text that clearly starts JSON but does not parse is reported with the parser's
position. What is left once the JSON is cut out is the instruction ("as slack", "compact", ...)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_FENCE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\n(.*?)\n?```", re.DOTALL)
_LOOKS_LIKE_JSON = re.compile(r"(?m)(?:^|:)[ \t]*(\{[ \t\r\n]*[\"}]|\[[ \t\r\n]*[\[{\"\d\-\]])")
TARGET_KEY = "target"


@dataclass(frozen=True)
class JsonError:
    message: str
    position: int

    def describe(self) -> str:
        return f"JSON error near character {self.position}: {self.message}"


@dataclass(frozen=True)
class Extracted:
    values: tuple[Any, ...] = ()
    instruction: str = ""
    target: str = ""          # a top-level "target" key, removed from the data
    error: JsonError | None = None


def extract(text: str) -> Extracted:
    fenced = list(_FENCE.finditer(text))
    if fenced:
        return _from_fences(text, fenced)
    values, spans = _balanced_values(text)
    if values:
        return _finish(values, _cut(text, spans))
    looks = _LOOKS_LIKE_JSON.search(text)
    if looks:
        start = looks.start(1)
        return Extracted(instruction=text[:start].strip(), error=_error_of(text[start:], start))
    return Extracted(instruction=text.strip())


def _from_fences(text: str, fenced: list[re.Match]) -> Extracted:
    values = []
    for match in fenced:
        body = match.group(1)
        if not body.strip():
            continue
        try:
            values.append(json.loads(body))
        except json.JSONDecodeError as err:
            return Extracted(instruction=_cut(text, [m.span() for m in fenced]),
                             error=JsonError(err.msg, match.start(1) + err.pos))
    return _finish(values, _cut(text, [m.span() for m in fenced]))


def _finish(values: list[Any], instruction: str) -> Extracted:
    flat: list[Any] = []
    for value in values:
        if isinstance(value, list) and len(value) > 1 and all(_is_envelope(v) for v in value):
            flat += value
        else:
            flat.append(value)
    target = ""
    cleaned = []
    for value in flat:
        if isinstance(value, dict) and isinstance(value.get(TARGET_KEY), str):
            target = target or value[TARGET_KEY]
            value = {k: v for k, v in value.items() if k != TARGET_KEY}
        cleaned.append(value)
    return Extracted(values=tuple(cleaned), instruction=instruction, target=target)


def _is_envelope(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("summary"), str)


def _balanced_values(text: str) -> tuple[list[Any], list[tuple[int, int]]]:
    """Every balanced region that parses as an object or a non-trivial array. A balanced region that
    does not parse is skipped whole, so nothing inside a broken object is picked up on its own."""
    values: list[Any] = []
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        if text[i] not in "{[":
            i += 1
            continue
        end = _balanced_end(text, i)
        if end is None:
            i += 1
            continue
        try:
            value = json.loads(text[i:end])
        except json.JSONDecodeError:
            i = end
            continue
        if _is_data(value):
            values.append(value)
            spans.append((i, end))
        i = end
    return values, spans


def _is_data(value: Any) -> bool:
    if isinstance(value, dict):
        return True
    return isinstance(value, list) and (len(value) >= 2 or any(isinstance(v, (dict, list, str)) for v in value))


def _balanced_end(text: str, start: int) -> int | None:
    """Index just past the bracket closing the one at `start`, string-aware; None if unbalanced."""
    stack: list[str] = []
    in_string = escaped = False
    pairs = {"{": "}", "[": "]"}
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            escaped, in_string = (False, True) if escaped else (ch == "\\", ch != '"')
            continue
        if ch == '"':
            in_string = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]":
            if not stack or stack.pop() != ch:
                return None
            if not stack:
                return i + 1
    return None


def _error_of(candidate: str, offset: int) -> JsonError:
    end = _balanced_end(candidate, 0)
    try:
        json.loads(candidate[:end] if end else candidate)
    except json.JSONDecodeError as err:
        return JsonError(err.msg, offset + err.pos)
    return JsonError("unexpected text after the JSON", offset)


def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    pieces, last = [], 0
    for start, end in spans:
        pieces.append(text[last:start])
        last = end
    pieces.append(text[last:])
    return " ".join(" ".join(pieces).split())
