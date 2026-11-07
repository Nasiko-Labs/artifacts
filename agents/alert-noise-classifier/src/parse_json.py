"""Pure: JSON → `Fire` records. Accepts an Alertmanager/Grafana webhook (`alerts[]` with labels, annotations,
status, startsAt, endsAt), a bare array of such alerts, or flat row objects (a collector step's envelope
`items` with `{name, time, ...}`). A previous classification envelope (`actionable`/`noise`) is not re-read."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fires import (
    ACKED,
    SELECTED_LABELS,
    Fire,
    finish,
    parse_duration,
    parse_time,
    to_float,
)
from parse_csv import LABEL_HEADERS, MAX_ROWS, SYNONYMS, norm

INSTANCE_LABELS = ("instance", "host", "pod", "node", "hostname")


@dataclass(frozen=True)
class JsonResult:
    fires: tuple[Fire, ...]
    rows_seen: int = 0
    rows_skipped: int = 0
    is_json: bool = False
    is_old_envelope: bool = False
    notes: tuple[str, ...] = ()


def load(text: str) -> Any:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    if not body.startswith(("{", "[")):
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def parse(text: str) -> JsonResult:
    data = load(text)
    if data is None:
        return JsonResult(())
    if isinstance(data, Mapping) and ("actionable" in data or "noise" in data) and "alerts" not in data:
        return JsonResult((), is_json=True, is_old_envelope=True)
    rows = _rows(data)
    fires, skipped = [], 0
    for i, obj in enumerate(rows[:MAX_ROWS]):
        fire = _from_alert(obj, i + 1) if isinstance(obj, Mapping) and "labels" in obj else _from_flat(obj, i + 1)
        if fire is None:
            skipped += 1
        else:
            fires.append(fire)
    notes = (f"Only the first {MAX_ROWS} alerts were read.",) if len(rows) > MAX_ROWS else ()
    return JsonResult(tuple(fires), min(len(rows), MAX_ROWS), skipped, True, False, notes)


def _rows(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, Mapping):
        for key in ("alerts", "items", "incidents", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))[:300]


def _from_alert(obj: Mapping[str, Any], index: int) -> Fire | None:
    labels = obj.get("labels") if isinstance(obj.get("labels"), Mapping) else {}
    notes = obj.get("annotations") if isinstance(obj.get("annotations"), Mapping) else {}
    name = str(labels.get("alertname") or obj.get("name") or "").strip()
    fired = parse_time(str(obj.get("startsAt") or ""))
    if not name or fired is None:
        return None
    selected = tuple(sorted((k, str(labels[k])) for k in SELECTED_LABELS if labels.get(k)))
    instance = next((str(labels[k]) for k in INSTANCE_LABELS if labels.get(k)), "")
    status = str(obj.get("status") or "").casefold()
    return finish(Fire(
        row=index, name=name, t_fire=fired, service=str(labels.get("service") or labels.get("job") or ""),
        severity=str(labels.get("severity") or labels.get("priority") or ""), labels=selected, instance=instance,
        t_resolve=parse_time(str(obj.get("endsAt") or "")) if status != "firing" else None, status=status,
        value=to_float(notes.get("value")) if notes.get("value") is not None else None,
        threshold=to_float(notes.get("threshold")) if notes.get("threshold") is not None else None,
        raw=_compact(obj),
    ))


def _flat_get(obj: Mapping[str, Any], role: str) -> str:
    by_norm = {norm(str(k)): v for k, v in obj.items()}
    for name in SYNONYMS[role]:
        value = by_norm.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _from_flat(obj: Any, index: int) -> Fire | None:
    if not isinstance(obj, Mapping):
        return None
    name, fired = _flat_get(obj, "name"), parse_time(_flat_get(obj, "fired"))
    if not name or fired is None:
        return None
    by_norm = {norm(str(k)): str(v) for k, v in obj.items() if v not in (None, "")}
    selected = tuple(sorted((LABEL_HEADERS[k], v) for k, v in by_norm.items() if k in LABEL_HEADERS))
    duration_key = next((k for k in by_norm if k in SYNONYMS["duration"]), "")
    unit = 60.0 if duration_key.endswith(("min", "mins", "minutes")) else 1.0
    status, ack_by = _flat_get(obj, "status").casefold(), _flat_get(obj, "ack")
    value, threshold = _flat_get(obj, "value"), _flat_get(obj, "threshold")
    return finish(Fire(
        row=index, name=name, t_fire=fired, service=_flat_get(obj, "service"), severity=_flat_get(obj, "severity"),
        labels=selected, instance=_flat_get(obj, "instance"), t_resolve=parse_time(_flat_get(obj, "resolved")),
        duration_s=parse_duration(_flat_get(obj, "duration"), unit), acked=bool(ack_by) or status in ACKED,
        acked_by=ack_by, note=_flat_get(obj, "note"), status=status,
        value=to_float(value) if value else None, threshold=to_float(threshold) if threshold else None,
        raw=_compact(obj),
    ))
