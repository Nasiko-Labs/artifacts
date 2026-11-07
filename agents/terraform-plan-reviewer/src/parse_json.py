"""Pure: parse `terraform show -json` (one object) and `terraform plan -json` (one JSON object per line).

show-json: `resource_changes[]` with before/after flattened to dotted keys (list indexes dropped, first value
kept, so `point_in_time_recovery.enabled` reads the same as in the human text); unknown values read
"(known after apply)", sensitive ones "(sensitive)" and are never compared. stream-json has no attribute
detail: only addresses, actions and the `change_summary` counts.
"""

from __future__ import annotations

import json
from typing import Any

from changes import SENSITIVE, UNKNOWN, Attr, Change, ParsedPlan, split_address

_ACTIONS = {("create",): "create", ("update",): "update", ("delete",): "delete", ("read",): "read",
            ("no-op",): "noop", ("delete", "create"): "replace", ("create", "delete"): "replace",
            ("forget",): "forget"}
_STREAM_ACTIONS = {"create": "create", "update": "update", "delete": "delete", "replace": "replace",
                   "read": "read", "noop": "noop", "no-op": "noop", "import": "import", "move": "move",
                   "forget": "forget"}
MAX_JSON_STARTS = 20
NO_ATTRIBUTES_NOTE = ("This is `terraform plan -json` output, which has no attribute detail: the data-loss and "
                      "exposure attribute checks were skipped. Paste `terraform show -json tfplan` for them.")


def load_object(text: str) -> dict | None:
    """The first JSON object in `text` (log lines before it are skipped), or None."""
    start, tries = text.find("{"), 0
    while start != -1 and tries < MAX_JSON_STARTS:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            value = None
        if isinstance(value, dict):
            return value
        start, tries = text.find("{", start + 1), tries + 1
    return None


def stream_objects(text: str) -> list[dict]:
    objects = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def parse_show(data: dict) -> ParsedPlan:
    changes = []
    for rc in data.get("resource_changes") or ():
        if isinstance(rc, dict):
            change = _show_change(rc)
            if change is not None:
                changes.append(change)
    drift = tuple(f"{d.get('address', '?')} changed outside of Terraform" for d in data.get("resource_drift") or ()
                  if isinstance(d, dict))
    has_actions = any(c.action not in ("noop", "read") for c in changes)
    return ParsedPlan("show-json", tuple(changes), drift, empty=not has_actions)


def _show_change(rc: dict) -> Change | None:
    change = rc.get("change") or {}
    actions = tuple(change.get("actions") or ())
    action = _ACTIONS.get(actions)
    if action is None:
        return None
    if change.get("importing") and action in ("noop", "update"):
        action = "import" if action == "noop" else action
    address = str(rc.get("address") or "")
    module, mode, rtype, _ = split_address(address)
    before, after = change.get("before"), change.get("after")
    unknown = _flatten(change.get("after_unknown"))
    sensitive = {**_flatten(change.get("before_sensitive")), **_flatten(change.get("after_sensitive"))}
    forces = {".".join(str(p) for p in path if not isinstance(p, int)) for path in change.get("replace_paths") or ()
              if isinstance(path, list)}
    flat_before, flat_after = _flatten(before), _flatten(after)
    attrs = []
    for key in dict.fromkeys([*flat_before, *flat_after, *(k for k, v in unknown.items() if v == "true")]):
        b, a = flat_before.get(key), flat_after.get(key)
        if unknown.get(key) == "true":
            a = UNKNOWN
        if sensitive.get(key) == "true":
            b, a = (SENSITIVE if b is not None else None), (SENSITIVE if a is not None else None)
        attrs.append(Attr(key, b, a, key in forces, f'resource_changes["{address}"].change.{key}'))
    return Change(address=address, resource_type=str(rc.get("type") or rtype), action=action,
                  mode=str(rc.get("mode") or mode), module_path=str(rc.get("module_address") or module),
                  reason=str(rc.get("action_reason") or ""), cbd=actions == ("create", "delete"), attrs=tuple(attrs),
                  before_text=json.dumps(before or {}, sort_keys=True), after_text=json.dumps(after or {}, sort_keys=True),
                  evidence=f'resource_changes["{address}"] actions {list(actions)}')


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Dotted keys to scalar values as plan text shows them; list indexes dropped, first value kept."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, inner in value.items():
            for k, v in _flatten(inner, f"{prefix}.{key}" if prefix else str(key)).items():
                out.setdefault(k, v)
    elif isinstance(value, list) and value and not any(isinstance(v, (dict, list)) for v in value):
        out[prefix] = ", ".join(_scalar(v) or "null" for v in value)
    elif isinstance(value, list):
        for inner in value:
            for k, v in _flatten(inner, prefix).items():
                out.setdefault(k, v)
    elif prefix:
        out[prefix] = _scalar(value)
    return out


def _scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def parse_stream(objects: list[dict]) -> ParsedPlan:
    changes: list[Change] = []
    drift: list[str] = []
    stated = None
    for obj in objects:
        kind = obj.get("type")
        change = obj.get("change") if isinstance(obj.get("change"), dict) else {}
        resource = change.get("resource") if isinstance(change.get("resource"), dict) else {}
        if kind == "planned_change":
            action = _STREAM_ACTIONS.get(str(change.get("action") or ""))
            address = str(resource.get("addr") or "")
            if action and address:
                module, mode, rtype, _ = split_address(address)
                changes.append(Change(address=address, resource_type=str(resource.get("resource_type") or rtype),
                                      action=action, mode=mode, module_path=str(resource.get("module") or module),
                                      reason=str(change.get("reason") or ""),
                                      evidence=f'planned_change {address} action "{change.get("action")}"'))
        elif kind == "resource_drift":
            drift.append(f"{resource.get('addr', '?')} changed outside of Terraform")
        elif kind == "change_summary":
            stated = {k: int(change.get(k) or 0) for k in ("add", "change", "import") if k in change}
            stated["destroy"] = int(change.get("remove") or 0)
    has_actions = any(c.action not in ("noop", "read") for c in changes)
    truncated = stated is None and bool(changes)
    caveats = ("The plan looks cut off (no `change_summary` line): only what was pasted is reviewed.",) if truncated \
        else ()
    return ParsedPlan("stream-json", tuple(changes), tuple(drift), stated, caveats, (NO_ATTRIBUTES_NOTE,),
                      hard=True, has_attributes=False, empty=not has_actions and stated is not None)
