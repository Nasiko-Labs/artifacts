"""Pure: argument-template replay. After the model makes one successful call for one value (a domain, a
call id, a company id), code repeats the same tool with the same argument shape for the other values,
substituting only the leaves that equalled the first value. No LLM call per value."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fence import Recorded

ID_KEYS = ("id", "companyId", "company_id", "objectId", "object_id", "hs_object_id", "callId", "call_id")


def _same(leaf: Any, value: str) -> bool:
    return isinstance(leaf, (str, int)) and not isinstance(leaf, bool) and str(leaf).casefold() == value.casefold()


def contains(arguments: Any, value: str) -> bool:
    """True when some leaf of the arguments equals `value` (case-insensitive)."""
    if isinstance(arguments, Mapping):
        return any(contains(v, value) for v in arguments.values())
    if isinstance(arguments, list):
        return any(contains(v, value) for v in arguments)
    return _same(arguments, value)


def substitute(arguments: Any, old: str, new: str) -> Any:
    """A copy with every leaf equal to `old` replaced by `new` (its type kept when it was a number)."""
    if isinstance(arguments, Mapping):
        return {k: substitute(v, old, new) for k, v in arguments.items()}
    if isinstance(arguments, list):
        return [substitute(v, old, new) for v in arguments]
    if _same(arguments, old):
        return int(new) if isinstance(arguments, int) and new.isdigit() else new
    return arguments


def template_for(records: Sequence[Recorded], values: Iterable[str]) -> tuple[Recorded, str] | None:
    """The first successful recorded call whose arguments carry one of `values`, and that value."""
    wanted = tuple(values)
    for record in records:
        if not record.ok:
            continue
        hit = next((v for v in wanted if contains(record.arguments, v)), None)
        if hit is not None:
            return record, hit
    return None


def covered(records: Sequence[Recorded], value: str) -> bool:
    return any(contains(r.arguments, value) for r in records)


def result_for(records: Sequence[Recorded], value: str) -> Recorded | None:
    """The last successful call whose arguments carry `value`."""
    return next((r for r in reversed(records) if r.ok and contains(r.arguments, value)), None)


def ids_near(text: str, value: str) -> tuple[str, ...]:
    """Ids of the JSON objects in a tool result that carry `value` (directly or one level down)."""
    try:
        data = json.loads(text)
    except ValueError:
        return ()
    found: list[str] = []
    _walk(data, value, found)
    return tuple(dict.fromkeys(found))


def _walk(node: Any, value: str, found: list[str]) -> None:
    if isinstance(node, list):
        for child in node:
            _walk(child, value, found)
        return
    if not isinstance(node, Mapping):
        return
    ident = next((node[k] for k in ID_KEYS if isinstance(node.get(k), (str, int)) and not isinstance(node.get(k), bool)),
                 None)
    if ident is not None and _carries(node, value, depth=2):
        found.append(str(ident))
    for child in node.values():
        _walk(child, value, found)


def _carries(node: Any, value: str, depth: int) -> bool:
    if isinstance(node, Mapping):
        return depth > 0 and any(_carries(v, value, depth - 1) for v in node.values())
    if isinstance(node, list):
        return depth > 0 and any(_carries(v, value, depth - 1) for v in node)
    return isinstance(node, str) and node.strip().casefold().removeprefix("www.") == value.casefold()
