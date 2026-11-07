"""Pure: gateway spend logs as JSON (a list of records, or an object holding one under data/logs/records/rows)
or JSONL (one record per line). Nested objects one level deep are flattened ("metadata.team" → column)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pandas as pd

from parse_csv import MAX_ROWS

LIST_KEYS = ("data", "logs", "records", "rows", "items", "usage", "results")


def records_of(obj: Any) -> list[dict] | None:
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, Mapping):
        rows = next((obj[k] for k in LIST_KEYS if isinstance(obj.get(k), list)), None)
        if rows is None:
            return None
    else:
        return None
    dicts = [r for r in rows if isinstance(r, Mapping)]
    return [_flat(r) for r in dicts] if dicts and len(dicts) == len(rows) else None


def _flat(record: Mapping[str, Any]) -> dict:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, Mapping):
            for sub, inner in value.items():
                if not isinstance(inner, (Mapping, list)):
                    out.setdefault(str(sub), inner)
        elif isinstance(value, list):
            out[str(key)] = ",".join(str(v) for v in value if not isinstance(v, (Mapping, list)))
        else:
            out[str(key)] = value
    return out


def parse_jsonl(text: str) -> list[dict] | None:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            return None
        if not isinstance(item, Mapping):
            return None
        rows.append(_flat(item))
    return rows or None


def read(records: list[dict]) -> tuple[pd.DataFrame, bool]:
    capped = len(records) > MAX_ROWS
    df = pd.DataFrame(records[:MAX_ROWS])
    df = df.astype(object).where(df.notna(), "").astype(str)
    return df, capped
