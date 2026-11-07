"""Pure: a `terraform show -json` plan to two resource sets, before and after the change.

create adds to after; delete is only in before; update is in both (priced on each side); replace is in both
(after priced as the new resource). no-op is in both, read (data sources) is ignored. References come from
the plan's `configuration` block (each resource's `references` lists), so attachments still link up when
ids are unknown until apply."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import resources as rs

REPLACE = ("delete", "create")


def is_plan(data: Any) -> bool:
    return isinstance(data, dict) and ("resource_changes" in data or ("format_version" in data and
                                                                       "planned_values" in data))


def change_kind(actions: list[str]) -> str:
    if sorted(actions) == sorted(REPLACE):
        return "replace"
    return actions[0] if len(actions) == 1 else "+".join(actions)


def parse(data: dict) -> tuple[list[dict], list[dict], list[str]]:
    """(before, after, notes)."""
    mentions = _config_mentions(data.get("configuration") or {})
    before, after, notes = [], [], []
    for rc in data.get("resource_changes") or []:
        if not isinstance(rc, Mapping) or rc.get("mode", "managed") != "managed":
            continue
        change = rc.get("change") if isinstance(rc.get("change"), Mapping) else {}
        actions = [str(a) for a in change.get("actions") or []]
        kind = change_kind(actions) if actions else "unknown"
        address, rtype = str(rc.get("address") or ""), str(rc.get("type") or "")
        if not address or not rtype or kind == "read":
            continue
        if kind == "unknown":
            notes.append(f"`{address}` has no planned actions; skipped.")
            continue
        if isinstance(change.get("before"), Mapping) and kind != "create":
            before.append(_resource(address, rtype, change["before"], kind))
        if isinstance(change.get("after"), Mapping) and kind != "delete":
            after.append(_resource(address, rtype, change["after"], kind))
    by_base = {a["address"]: mentions.get(rs.base_address(a["address"]), "") + rs.dump(a["attrs"])
               for a in after + before}
    return rs.link(before, by_base, _key), rs.link(after, by_base, _key), notes


def _key(res: Mapping[str, Any]) -> str:
    return rs.base_address(res["address"])


def _resource(address: str, rtype: str, values: Mapping[str, Any], kind: str) -> dict:
    tags = values.get("tags") if isinstance(values.get("tags"), Mapping) else None
    if tags is None and isinstance(values.get("tags_all"), Mapping):
        tags = values["tags_all"]
    attrs = {k: v for k, v in values.items() if k not in ("tags", "tags_all") and v is not None}
    return rs.make(address, rtype, rtype, attrs, tags or {}, True, 1, kind)


def _config_mentions(configuration: Mapping[str, Any]) -> dict[str, str]:
    """resource address (no index) -> every reference its expressions make, as text."""
    out: dict[str, str] = {}
    root = configuration.get("root_module") if isinstance(configuration.get("root_module"), Mapping) else {}
    for res in root.get("resources") or []:
        if isinstance(res, Mapping) and res.get("address"):
            out[str(res["address"])] = " ".join(_references(res.get("expressions") or {}))
    return out


def _references(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        refs = [str(r) for r in value.get("references") or []] if isinstance(value.get("references"), list) else []
        return refs + [r for k, v in value.items() if k != "references" for r in _references(v)]
    if isinstance(value, list):
        return [r for v in value for r in _references(v)]
    return []
