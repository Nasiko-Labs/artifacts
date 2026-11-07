"""Pure: a CSV/TSV export with a header row → `Fire` records. Headers are mapped to roles through a synonym
table; when the alert-name or fired-time column cannot be identified, the result names the missing role so
the agent can ask which column it is (overrides carry the answer)."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass

from fires import (
    ACKED,
    SELECTED_LABELS,
    Fire,
    finish,
    parse_duration,
    parse_time,
    to_float,
)

MAX_ROWS = 5_000
REQUIRED = ("name", "fired")
SYNONYMS: dict[str, tuple[str, ...]] = {
    "name": ("alert", "alertname", "alert_name", "title", "summary", "description", "incident", "incident_title",
             "name", "rule", "check"),
    "fired": ("triggered_at", "created_at", "created_on", "fired_at", "start", "started_at", "startsat", "time",
              "timestamp", "triggered", "created", "opened_at", "date"),
    "resolved": ("resolved_at", "end", "ended_at", "endsat", "resolved", "closed_at", "resolved_on"),
    "duration": ("duration", "duration_seconds", "duration_secs", "duration_s", "duration_min", "duration_mins",
                 "duration_minutes", "time_to_resolve", "ttr", "seconds_to_resolve"),
    "ack": ("acknowledged_by", "ack_by", "acked_by", "assignee", "acknowledger", "responder"),
    "severity": ("urgency", "severity", "priority", "sev", "level"),
    "service": ("service", "component", "team", "labels_service", "service_name", "app", "job"),
    "note": ("note", "notes", "resolution", "comment", "comments", "resolve_reason", "resolution_note",
             "resolution_notes", "action"),
    "status": ("status", "state"),
    "instance": ("host", "hostname", "instance", "pod", "node", "labels_instance", "labels_pod"),
    "value": ("value", "observed_value", "metric_value", "current_value"),
    "threshold": ("threshold", "limit", "trigger_threshold"),
}
LABEL_HEADERS = {label: label for label in SELECTED_LABELS} | {"labels_env": "env", "labels_cluster": "cluster"}
_NORM = re.compile(r"[\s.\-/]+")


def norm(header: str) -> str:
    return _NORM.sub("_", header.strip().strip('"').casefold()).strip("_")


@dataclass(frozen=True)
class CsvResult:
    fires: tuple[Fire, ...]
    headers: tuple[str, ...]
    missing: str = ""                   # "name" | "fired" when that column cannot be identified
    rows_seen: int = 0
    rows_skipped: int = 0
    notes: tuple[str, ...] = ()


def delimiter_of(line: str) -> str:
    counts = {d: line.count(d) for d in (",", "\t", ";", "|")}
    return max(counts, key=counts.get) if max(counts.values()) else ","


def headers_of(text: str) -> list[str]:
    first = next((x for x in text.splitlines() if x.strip()), "")
    return [h.strip() for h in next(csv.reader([first], delimiter=delimiter_of(first)), [])]


def map_columns(headers: list[str], overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """role → header. Synonym order decides between two matching headers; overrides win."""
    by_norm = {norm(h): h for h in headers}
    roles: dict[str, str] = {}
    for role, names in SYNONYMS.items():
        hit = next((by_norm[n] for n in names if n in by_norm), None)
        if hit is not None and hit not in roles.values():
            roles[role] = hit
    for role, header in (overrides or {}).items():
        if header in headers:
            roles = {r: h for r, h in roles.items() if h != header} | {role: header}
    return roles


def match_header(answer: str, headers: list[str]) -> str | None:
    wanted = norm(answer)
    return next((h for h in headers if norm(h) == wanted), None)


def parse(text: str, overrides: Mapping[str, str] | None = None) -> CsvResult:
    headers = headers_of(text)
    roles = map_columns(headers, overrides)
    missing = next((r for r in REQUIRED if r not in roles), "")
    if missing:
        return CsvResult((), tuple(headers), missing)
    first = next(x for x in text.splitlines() if x.strip())
    reader = csv.DictReader(io.StringIO(text.strip()), delimiter=delimiter_of(first))
    lines = [x for x in text.strip().splitlines() if x.strip()][1:]
    labels = {h: LABEL_HEADERS[norm(h)] for h in headers if norm(h) in LABEL_HEADERS}
    unit = 60.0 if norm(roles.get("duration", "")).endswith(("min", "mins", "minutes")) else 1.0
    fires, skipped, seen = [], 0, 0
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            break
        seen += 1
        fire = _fire(row, roles, labels, unit, i + 1, lines[i] if i < len(lines) else "")
        if fire is None:
            skipped += 1
        else:
            fires.append(fire)
    notes = (f"Only the first {MAX_ROWS} rows were read.",) if seen >= MAX_ROWS else ()
    return CsvResult(tuple(fires), tuple(headers), "", seen, skipped, notes)


def _get(row: Mapping[str, str | None], roles: Mapping[str, str], role: str) -> str:
    header = roles.get(role)
    return str(row.get(header) or "").strip() if header else ""


def _fire(row, roles, labels, unit: float, index: int, raw: str) -> Fire | None:
    name, fired = _get(row, roles, "name"), parse_time(_get(row, roles, "fired"))
    if not name or fired is None:
        return None
    status = _get(row, roles, "status").casefold()
    ack_by = _get(row, roles, "ack")
    selected = tuple(sorted((label, str(row.get(h) or "").strip()) for h, label in labels.items()
                            if str(row.get(h) or "").strip()))
    return finish(Fire(
        row=index, name=name, t_fire=fired, service=_get(row, roles, "service"),
        severity=_get(row, roles, "severity"), labels=selected, instance=_get(row, roles, "instance"),
        t_resolve=parse_time(_get(row, roles, "resolved")),
        duration_s=parse_duration(_get(row, roles, "duration"), unit), acked=bool(ack_by) or status in ACKED,
        acked_by=ack_by, note=_get(row, roles, "note"), status=status,
        value=to_float(_get(row, roles, "value")) if roles.get("value") else None,
        threshold=to_float(_get(row, roles, "threshold")) if roles.get("threshold") else None, raw=raw.strip(),
    ))
