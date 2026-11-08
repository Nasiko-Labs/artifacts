"""Pure: pasted reviews → `Review`s. Three shapes, detected in this order: a JSON list (or `{"reviews": [...]}`),
CSV/TSV with a header row, and blocks separated by blank lines or `---` with optional `Key: value` lines.

`App: Name` applies to the reviews after it; `Ours: Name` names our app (else the first app named). Text
before the reviews is the user's instruction and is never parsed as a review."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import normalise
import versions
from records import Review

ALIASES = {
    "rating": "rating", "stars": "rating", "star": "rating", "score": "rating", "version": "version",
    "app version": "version", "app_version": "version", "appversion": "version", "date": "date",
    "store": "store", "platform": "store", "app": "app", "app name": "app", "app_name": "app", "title": "title",
    "body": "body", "review": "body", "text": "body", "content": "body", "comment": "body", "ours": "ours",
}
_REVIEW_KEYS = frozenset({"rating", "version", "date", "store", "title", "body"})
_KEY_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z _]{0,15}):\s*(.*)$")
_SEPARATOR = re.compile(r"^\s*(?:-{3,}|\s*)$")
_OURS_LINE = re.compile(r"^\s*ours:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_APP_LINE = re.compile(r"^\s*app:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Parsed:
    reviews: tuple[Review, ...] = ()
    skipped: int = 0
    ours: str | None = None
    apps: tuple[str, ...] = ()
    shape: str = ""


def parse(text: str, start: int = 1) -> Parsed:
    for shape, rows in (("json", _json_rows(text)), ("csv", _csv_rows(text)), ("blocks", _block_rows(text))):
        if rows:
            return _build(rows, shape, start, _ours(text))
    return Parsed(ours=_ours(text))


def _ours(text: str) -> str | None:
    found = _OURS_LINE.search(text)
    return found.group(1) if found else None


def _build(rows: list[dict[str, Any]], shape: str, start: int, ours: str | None) -> Parsed:
    reviews: list[Review] = []
    skipped = 0
    for row in rows:
        body = str(row.get("body") or "").strip()
        if not body:
            skipped += 1
            continue
        reviews.append(Review(
            id=f"R{start + len(reviews)}", app=str(row.get("app") or "").strip() or "Our app", body=body,
            rating=normalise.rating(row.get("rating")), version=versions.normalise(row.get("version")),
            date=normalise.iso_date(row.get("date")), store=normalise.store(row.get("store")),
            title=str(row.get("title") or "").strip()))
    apps = tuple(dict.fromkeys(r.app for r in reviews))
    return Parsed(tuple(reviews), skipped, ours, apps, shape)


def _canonical(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw_key, value in row.items():
        key = ALIASES.get(str(raw_key).strip().casefold().replace("-", " "))
        if key and key not in out:
            out[key] = value
    return out


def _json_rows(text: str) -> list[dict[str, Any]]:
    for opener, closer in (("[", "]"), ("{", "}")):
        first, last = text.find(opener), text.rfind(closer)
        if first == -1 or last <= first:
            continue
        try:
            data = json.loads(text[first:last + 1])
        except ValueError:
            continue
        if isinstance(data, Mapping):
            data = data.get("reviews")
        if isinstance(data, list) and data and all(isinstance(r, Mapping) for r in data):
            rows = [_canonical(r) for r in data]
            if any("body" in r for r in rows):
                return rows
    return []


def _csv_rows(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines[:40]):
        delimiter = "\t" if "\t" in line else ","
        header = [ALIASES.get(c.strip().casefold().replace("-", " ")) for c in next(csv.reader([line], delimiter=delimiter))]
        if "body" not in header or sum(1 for h in header if h) < 2:
            continue
        before = "\n".join(lines[:index])
        default_app = _APP_LINE.findall(before)[-1] if _APP_LINE.search(before) else ""
        reader = csv.reader(io.StringIO("\n".join(lines[index + 1:])), delimiter=delimiter)
        rows = [{h: cell for h, cell in zip(header, cells, strict=False) if h} for cells in reader if any(cells)]
        return [{**r, "app": r.get("app") or default_app} for r in rows]
    return []


def _blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if _SEPARATOR.match(line):
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line)
    return [b for b in blocks if b]


def _split_block(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    keys: dict[str, str] = {}
    body: list[str] = []
    for line in lines:
        match = _KEY_LINE.match(line)
        key = ALIASES.get(match.group(1).strip().casefold()) if match else None
        if key and key not in keys:
            keys[key] = match.group(2).strip()
        else:
            body.append(line.strip())
    if body:
        keys["body"] = "\n".join([keys["body"], *body] if keys.get("body") else body).strip()
    return keys, body


def _block_rows(text: str) -> list[dict[str, Any]]:
    split = [_split_block(block) for block in _blocks(text)]
    keyed = [i for i, (keys, _) in enumerate(split) if set(keys) - {"body"}]
    first = keyed[0] if keyed else (min(len(split), 1))
    rows: list[dict[str, Any]] = []
    app = ""
    for keys, _ in split[first:]:
        header = keys.keys() & {"app", "ours"}
        app = keys.get("app") or keys.get("ours") or app
        if keys.keys() & (_REVIEW_KEYS - {"body"}) or ("body" in keys and not header):
            rows.append({**keys, "app": app})
    return rows
