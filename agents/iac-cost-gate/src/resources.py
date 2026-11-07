"""Pure: the parsed-resource shape every parser produces (plain dicts, so they checkpoint as JSON) and the
helpers shared by pricing and the waste rules.

    {"address", "type" (terraform-style), "source_type" (as written), "attrs", "tags", "tags_known",
     "qty", "referenced_by" (types of resources that mention this one), "change" (plan only)}
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

UNRESOLVED = "${"
_INDEX = re.compile(r"\[[^\]]*\]$")


def make(address: str, rtype: str, source_type: str, attrs: Mapping[str, Any], tags: Mapping[str, Any] | None,
         tags_known: bool = True, qty: int = 1, change: str = "") -> dict:
    return {"address": address, "type": rtype, "source_type": source_type, "attrs": dict(attrs),
            "tags": {str(k): str(v) for k, v in (tags or {}).items()}, "tags_known": tags_known, "qty": qty,
            "referenced_by": [], "change": change}


def is_unresolved(value: Any) -> bool:
    return isinstance(value, str) and UNRESOLVED in value


def to_number(value: Any) -> float | None:
    """A literal number (or numeric string); None for anything else, including expressions."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().casefold() == "true"


def text(value: Any) -> str:
    """A literal string attribute, or "" when it is missing or not a resolved literal."""
    if isinstance(value, str) and not is_unresolved(value):
        return value.strip()
    return ""


def base_address(address: str) -> str:
    return _INDEX.sub("", address)


def link(resources: list[dict], mentions: Mapping[str, str], key_of) -> list[dict]:
    """New resources with `referenced_by` filled: the types of other resources whose mention text
    (`mentions[address]`) names this one's reference key (`key_of(resource)`)."""
    patterns = {r["address"]: re.compile(rf"(?<![\w.:-]){re.escape(key_of(r))}(?![\w-])") for r in resources}
    linked = []
    for res in resources:
        pattern = patterns[res["address"]]
        by = sorted({o["type"] for o in resources if o is not res and o["address"] != res["address"]
                     and pattern.search(mentions.get(o["address"], ""))})
        linked.append({**res, "referenced_by": by})
    return linked


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def count_types(resources: Iterable[Mapping[str, Any]], rtype: str) -> int:
    return sum(1 for r in resources if r["type"] == rtype)
