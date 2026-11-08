"""Pure: parse `terraform show -json` output (`resource_changes[]`).

Actions: `["create"]`, `["update"]`, `["delete"]`, `["delete","create"]` / `["create","delete"]` = replace,
`["read"]`, `["no-op"]` (ignored). Changed keys are the top-level keys whose before and after differ (plus
keys unknown until apply); values marked sensitive are never kept.
"""

from __future__ import annotations

import json
from typing import Any

from plan_model import SENSITIVE, UNKNOWN, Attr, Change, ParsedPlan, split_address

_ACTIONS = {("create",): "create", ("update",): "update", ("delete",): "delete", ("read",): "read",
            ("no-op",): "noop", ("delete", "create"): "replace", ("create", "delete"): "replace"}
MAX_JSON_STARTS = 20


def load_object(text: str) -> dict | None:
    """The first JSON object in `text` (lines before it are skipped), or None."""
    start, tries = (text or "").find("{"), 0
    while start != -1 and tries < MAX_JSON_STARTS:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            value = None
        if isinstance(value, dict):
            return value
        start, tries = text.find("{", start + 1), tries + 1
    return None


def object_span(text: str) -> tuple[int, int] | None:
    """Where the first JSON plan object sits in `text`, so the words around it can be read as the request."""
    start, tries = (text or "").find("{"), 0
    while start != -1 and tries < MAX_JSON_STARTS:
        try:
            value, end = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            value, end = None, 0
        if isinstance(value, dict) and is_plan(value):
            return start, start + end
        start, tries = text.find("{", start + 1), tries + 1
    return None


def is_plan(data: dict) -> bool:
    return isinstance(data.get("resource_changes"), list) or (
        "format_version" in data and "terraform_version" in data)


def parse(data: dict) -> ParsedPlan:
    changes = tuple(c for c in (_change(rc) for rc in data.get("resource_changes") or () if isinstance(rc, dict))
                    if c is not None)
    return ParsedPlan("json", changes, None, str(data.get("workspace") or ""))


def _change(rc: dict) -> Change | None:
    change = rc.get("change") if isinstance(rc.get("change"), dict) else {}
    action = _ACTIONS.get(tuple(change.get("actions") or ()))
    address = str(rc.get("address") or "")
    if action is None or not address:
        return None
    module, mode, rtype, name = split_address(address)
    before = change.get("before") if isinstance(change.get("before"), dict) else {}
    after = change.get("after") if isinstance(change.get("after"), dict) else {}
    unknown = change.get("after_unknown") if isinstance(change.get("after_unknown"), dict) else {}
    sensitive = {**_truthy(change.get("before_sensitive")), **_truthy(change.get("after_sensitive"))}
    forces = {str(p[0]) for p in change.get("replace_paths") or () if isinstance(p, list) and p}
    keys = _changed_keys(action, before, after, unknown)
    attrs = tuple(Attr(k, _shown(before.get(k), k in sensitive), UNKNOWN if unknown.get(k) is True
                       else _shown(after.get(k), k in sensitive), k in forces) for k in keys)
    known = tuple((k, v) for k, v in {**_plain(before, sensitive), **_plain(after, sensitive)}.items())
    previous = str(rc.get("previous_address") or "")
    if action == "noop" and previous:
        action = "move"
    return Change(address=address, rtype=str(rc.get("type") or rtype), name=name, module_path=str(rc.get("module_address") or module),
                  mode=str(rc.get("mode") or mode), action=action, attrs=attrs, moved_from=previous, known=known)


def _changed_keys(action: str, before: dict, after: dict, unknown: dict) -> list[str]:
    if action == "create":
        return list(dict.fromkeys([*after, *(k for k, v in unknown.items() if v is True)]))
    if action == "delete":
        return list(before)
    if action in ("read", "noop"):
        return []
    keys = dict.fromkeys([*before, *after, *(k for k, v in unknown.items() if v is True)])
    return [k for k in keys if before.get(k) != after.get(k) or unknown.get(k) is True]


def _truthy(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    return {str(k): True for k, v in value.items() if v is True or (isinstance(v, (dict, list)) and v)}


def _shown(value: Any, sensitive: bool) -> str | None:
    if value is None:
        return None
    if sensitive:
        return SENSITIVE
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)[:200]
    return str(value)


def _plain(values: dict, sensitive: dict[str, bool]) -> dict[str, str]:
    return {str(k): str(v) for k, v in values.items()
            if isinstance(v, (str, int, float)) and not isinstance(v, bool) and k not in sensitive and str(v)}
