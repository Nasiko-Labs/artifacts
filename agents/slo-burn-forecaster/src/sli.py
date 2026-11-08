"""Pure: SLI rows out of the user's message: a CSV, TSV or markdown table, JSON lines, or an envelope
(`services[{name, slo, period_days, series[]}]`). Headers are matched case-insensitively against known aliases.
A row that cannot be read (bad time, negative counts, errors above total, an error rate above 1) is counted as
rejected, never guessed. The model never reads a row."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from values import number as _number
from values import parse_time
from values import rate as _rate

DEFAULT_SERVICE = "service"
_ALIASES: dict[str, frozenset[str]] = {
    "timestamp": frozenset({"timestamp", "time", "ts", "date", "datetime", "bucket", "hour", "period_start"}),
    "service": frozenset({"service", "svc", "service_name", "app", "application", "component"}),
    "total": frozenset({"total", "requests", "total_requests", "count", "events", "valid", "req", "total_events"}),
    "errors": frozenset({"errors", "error", "bad", "failed", "failures", "error_count", "bad_events", "5xx"}),
    "good": frozenset({"good", "success", "successes", "ok", "good_events"}),
    "error_rate": frozenset({"error_rate", "err_rate", "errorrate", "error_ratio", "error_pct", "error_percent"}),
}
_NORMALISE = re.compile(r"[\s\-]+")
_MD_SEPARATOR = re.compile(r"^\|?\s*:?-{2,}")
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class Sample:
    """One bucket as given. `total`/`errors` are counts; `rate` is an error fraction given without counts."""

    service: str
    ts: int
    total: Decimal | None = None
    errors: Decimal | None = None
    rate: Decimal | None = None

    def to_list(self) -> list:
        return [self.service, self.ts, _s(self.total), _s(self.errors), _s(self.rate)]

    @staticmethod
    def from_list(raw: Sequence[Any]) -> Sample:
        return Sample(str(raw[0]), int(raw[1]), _d(raw[2]), _d(raw[3]), _d(raw[4]))


@dataclass(frozen=True)
class EnvelopeSlo:
    service: str
    percent: str
    period_days: int | None


@dataclass(frozen=True)
class Parsed:
    samples: tuple[Sample, ...] = ()
    rejected: int = 0
    table_lines: frozenset[int] = frozenset()   # line indexes that were part of the table
    slos: tuple[EnvelopeSlo, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.samples) or self.rejected > 0


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _d(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def parse(text: str) -> Parsed:
    """The SLI data in the text: an envelope, else JSON lines, else the first delimited table."""
    return _envelope(text) or _json_lines(text) or _table(text) or Parsed()


# --- shared row reading ----------------------------------------------------------------------------------------

def field_of(header: str) -> str | None:
    key = _NORMALISE.sub("_", header.strip().strip("`*").casefold())
    return next((name for name, aliases in _ALIASES.items() if key in aliases), None)


def _mapping(headers: Sequence[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, header in enumerate(headers):
        name = field_of(header)
        if name is not None and name not in found:
            found = {**found, name: index}
    return found


def _usable(fields: Mapping[str, Any]) -> bool:
    has_counts = "total" in fields and ("errors" in fields or "good" in fields)
    return "timestamp" in fields and (has_counts or "error_rate" in fields)


def sample_of(values: Mapping[str, Any], thousands: bool = True) -> Sample | None:
    """One row as a Sample, or None when it is invalid."""
    ts = parse_time(values.get("timestamp"))
    if ts is None:
        return None
    service = str(values.get("service") or DEFAULT_SERVICE).strip() or DEFAULT_SERVICE
    total = _number(values.get("total"), thousands) if values.get("total") not in (None, "") else None
    if values.get("error_rate") not in (None, "") and values.get("errors") in (None, "") \
            and values.get("good") in (None, ""):
        return _rate_sample(service, ts, _rate(values.get("error_rate"), thousands), total)
    return _count_sample(service, ts, total, values, thousands)


def _rate_sample(service: str, ts: int, rate: Decimal | None, total: Decimal | None) -> Sample | None:
    if rate is None or rate < 0 or rate > 1:
        return None
    if total is None:
        return Sample(service, ts, rate=rate)
    return None if total < 0 else Sample(service, ts, total, rate * total)


def _count_sample(service: str, ts: int, total: Decimal | None, values: Mapping[str, Any],
                  thousands: bool) -> Sample | None:
    if total is None or total < 0:
        return None
    if values.get("errors") not in (None, ""):
        errors = _number(values.get("errors"), thousands)
    else:
        good = _number(values.get("good"), thousands)
        errors = None if good is None or good < 0 else total - good
    if errors is None or errors < 0 or errors > total:
        return None
    return Sample(service, ts, total, errors)


# --- envelope --------------------------------------------------------------------------------------------------

def _envelope(text: str) -> Parsed | None:
    match = _FENCED_JSON.search(text)
    body = match.group(1) if match else text.strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    services = data.get("services") if isinstance(data, Mapping) else None
    if not isinstance(services, list):
        return None
    samples, rejected, slos = [], 0, []
    for entry in services:
        if not isinstance(entry, Mapping) or not str(entry.get("name") or "").strip():
            continue
        name = str(entry["name"]).strip()
        if entry.get("slo") is not None:
            days = entry.get("period_days")
            slos.append(EnvelopeSlo(name, str(entry["slo"]).rstrip("%"), int(days) if _is_int(days) else None))
        for point in entry.get("series") or []:
            sample = _object_sample({**point, "service": name}) if isinstance(point, Mapping) else None
            if sample:
                samples.append(sample)  # local accumulator
            else:
                rejected += 1
    if not samples and not rejected:
        return None
    return Parsed(tuple(samples), rejected, frozenset(), tuple(slos))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) or (isinstance(value, str) and value.isdigit())


def _object_sample(obj: Mapping[str, Any]) -> Sample | None:
    fields = {field_of(str(k)): v for k, v in obj.items() if field_of(str(k))}
    return sample_of(fields, thousands=False) if _usable(fields) else None


# --- JSON lines ------------------------------------------------------------------------------------------------

def _json_lines(text: str) -> Parsed | None:
    lines = text.splitlines()
    samples, rejected, used = [], 0, set()
    for index, line in enumerate(lines):
        stripped = line.strip().rstrip(",")
        if not stripped.startswith("{"):
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(obj, Mapping):
            continue
        fields = {field_of(str(k)) for k in obj}
        if "timestamp" not in fields:
            continue  # an SLO line or something else
        used.add(index)
        sample = _object_sample(obj)
        if sample:
            samples.append(sample)  # local accumulator
        else:
            rejected += 1
    if len(used) < 2:
        return None
    return Parsed(tuple(samples), rejected, frozenset(used))


# --- delimited tables ------------------------------------------------------------------------------------------

def _delimiter(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("|") and stripped.count("|") >= 3:
        return "|"
    return next((d for d in ("\t", ",", ";") if d in stripped), None)


def _split(line: str, delimiter: str) -> list[str]:
    stripped = line.strip()
    if delimiter == "|":
        stripped = stripped.strip("|")
    return [cell.strip() for cell in stripped.split(delimiter)]


def _table(text: str) -> Parsed | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        delimiter = _delimiter(line)
        if delimiter is None:
            continue
        fields = _mapping(_split(line, delimiter))
        if _usable(fields):
            return _rows(lines, index, delimiter, fields)
    return None


def _rows(lines: Sequence[str], header_at: int, delimiter: str, fields: Mapping[str, int]) -> Parsed:
    width = len(_split(lines[header_at], delimiter))
    samples, rejected, used = [], 0, {header_at}
    thousands = delimiter != ","
    for index in range(header_at + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            if samples or rejected:
                break
            continue
        if delimiter == "|" and _MD_SEPARATOR.match(line.strip()):
            used.add(index)
            continue
        cells = _split(line, delimiter) if delimiter in line else []
        if len(cells) != width:
            if any(parse_time(c) is not None for c in cells):
                used.add(index)
                rejected += 1
                continue
            break  # the table ended (an SLO line or a question follows)
        used.add(index)
        sample = sample_of({name: cells[i] for name, i in fields.items()}, thousands)
        if sample:
            samples.append(sample)  # local accumulator
        else:
            rejected += 1
    return Parsed(tuple(samples), rejected, frozenset(used))
