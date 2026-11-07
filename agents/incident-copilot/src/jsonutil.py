"""Pure JSON helpers: read the model's JSON answer, and walk a tool's JSON output by shape."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

_FENCED_JSON = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def load_json_object(text: str) -> dict | None:
    """The first JSON object in a model answer (bare, fenced in ``` or wrapped in prose), else None."""
    raw = (text or "").strip()
    match = _FENCED_JSON.search(raw)
    candidates = [match.group(1).strip()] if match else []
    candidates.append(raw)
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def text_of(value: Any) -> str:
    """A model field as stripped text; null and non-scalars become ""."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def walk_dicts(value: Any, depth: int = 0) -> Iterator[dict]:
    """Every dict inside a JSON value, depth-first (bounded, so a hostile body cannot recurse forever)."""
    if depth > 40:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child, depth + 1)


def walk_lists(value: Any, depth: int = 0) -> Iterator[tuple[str, list]]:
    """Every list inside a JSON value with the key it sits under."""
    if depth > 40:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, list):
                yield str(key), child
            yield from walk_lists(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from walk_lists(child, depth + 1)


def first_key(record: dict, keys: tuple[str, ...]) -> Any:
    """The value of the first key present (case-insensitive) with a non-empty value."""
    lowered = {str(k).casefold(): v for k, v in record.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        if value not in (None, "", [], {}):
            return value
    return None
