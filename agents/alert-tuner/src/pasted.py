"""Pure: data the user pasted into the message.

- Metric values at firing, one alert per line: `Disk usage: 82, 85, 91, 88` (or "values at firing 82, ...").
  Used for threshold proposals only.
- An incident export (CSV with a header, or a JSON list) when no incident pager is connected: columns
  title, service, created, resolved, acknowledged_by / acknowledged_at, notes (a count), optional
  resolved_by, number, url. A row becomes an incident plus the log facts the columns state.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from noise import LogFacts
from records import Incident, LogEntry

MAX_ROWS = 2000
_NUM = r"-?\d+(?:\.\d+)?"
_VALUES_LINE = re.compile(rf"^\s*(?P<name>[^:\n]{{2,80}}?)\s*:\s*(?:values?\s+(?:at|when)\s+firing\s*:?\s*)?"
                          rf"(?P<nums>{_NUM}(?:\s*,\s*{_NUM})+)\s*$", re.IGNORECASE | re.MULTILINE)
_SYSTEM_ACTORS = frozenset({"", "system", "auto", "automatic", "auto-resolve", "integration", "service", "monitor"})
_NAME_SUFFIX = re.compile(r"\s+(?:alert|alerts|values?)$", re.IGNORECASE)


@dataclass(frozen=True)
class Export:
    incidents: tuple[Incident, ...]
    facts: Mapping[str, LogFacts]
    truncated: bool = False


def metric_values(text: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for m in _VALUES_LINE.finditer(text or ""):
        name = _NAME_SUFFIX.sub("", m.group("name").strip())
        if "," in name or len(name.split()) > 8:
            continue
        out[name] = [float(v) for v in re.findall(_NUM, m.group("nums"))]
    return out


def export(text: str) -> Export | None:
    rows = _json_rows(text) or _csv_rows(text)
    if not rows:
        return None
    incidents, facts = [], {}
    for n, row in enumerate(rows[:MAX_ROWS], start=1):
        made = _row(row, n)
        if made is not None:
            incidents.append(made[0])
            facts[made[0].id] = made[1]
    return Export(tuple(incidents), facts, len(rows) > MAX_ROWS) if incidents else None


def _csv_rows(text: str) -> list[dict]:
    lines = (text or "").splitlines()
    start = next((i for i, line in enumerate(lines) if "title" in line.casefold() and "created" in line.casefold()
                  and line.count(",") >= 2), None)
    if start is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    return [{(k or "").strip().casefold(): (v or "").strip() for k, v in row.items()} for row in reader]


def _json_rows(text: str) -> list[dict]:
    begin = (text or "").find("[")
    if begin == -1:
        return []
    try:
        data = json.loads(text[begin:text.rfind("]") + 1])
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [{str(k).casefold(): "" if v is None else str(v) for k, v in row.items()} for row in data
            if isinstance(row, dict) and "title" in {str(k).casefold() for k in row}]


def _row(row: Mapping[str, str], n: int) -> tuple[Incident, LogFacts] | None:
    title, created = row.get("title", ""), row.get("created", "") or row.get("created_at", "")
    if not title or not created:
        return None
    resolved = row.get("resolved", "") or row.get("resolved_at", "")
    acked = bool(row.get("acknowledged_by") or row.get("acknowledged_at") or row.get("acknowledged"))
    resolver = row.get("resolved_by", "").casefold()
    number = row.get("number") or row.get("incident_number") or row.get("id") or str(n)
    incident = Incident(id=f"row-{n}", number=number, title=title, service=row.get("service", ""),
                        status="resolved" if resolved else "triggered", created=created, resolved=resolved,
                        url=row.get("url", ""), resolved_by="human" if resolver not in _SYSTEM_ACTORS else "")
    entries = (LogEntry(incident.id, "acknowledge", "human"),) if acked else ()
    notes = _count(row.get("notes") or row.get("notes_count") or "0")
    return incident, LogFacts(read=True, entries=entries, notes=notes)


def _count(text: str) -> int:
    digits = re.match(r"\s*(\d+)", text)
    return int(digits.group(1)) if digits else (1 if text.strip() else 0)
