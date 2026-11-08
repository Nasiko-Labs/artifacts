"""Pure: PR exports pasted as CSV/TSV (a header with `title` and `author|user`) or as a JSON array of
objects. Field names follow common code-host exports."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from typing import Any

import dates
from records import PR, Record

FIELDS = {
    "title": ("title", "name", "subject", "summary"),
    "author": ("author", "user", "login", "author_login", "creator", "by", "opened_by", "username"),
    "date": ("merged_at", "merged", "date", "closed_at", "created_at", "updated_at"),
    "url": ("url", "html_url", "link", "web_url"),
    "number": ("number", "pr", "id", "iid", "#", "pr_number"),
    "body": ("body", "description", "desc"),
}
_NUMBER = re.compile(r"\d{1,7}")


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9#]+", "_", (name or "").strip().casefold()).strip("_")


def delimiter_of(line: str) -> str:
    return "\t" if line.count("\t") > line.count(",") else ","


def is_header(line: str) -> bool:
    if "," not in line and "\t" not in line:
        return False
    cols = {norm(c) for c in line.split(delimiter_of(line))}
    return bool(cols & set(FIELDS["title"])) and bool(cols & set(FIELDS["author"]) | cols & set(FIELDS["url"]))


def _pick(row: dict[str, Any], key: str) -> Any:
    normed = {norm(k): v for k, v in row.items() if k is not None}
    return next((normed[n] for n in FIELDS[key] if normed.get(n) not in (None, "")), "")


def _author(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("login") or value.get("name") or value.get("username") or ""
    return str(value or "").strip()


def from_row(row: dict[str, Any], today: date) -> Record | None:
    title = str(_pick(row, "title") or "").strip()
    if not title:
        return None
    iso, _ = dates.find(str(_pick(row, "date") or ""), today)
    url = str(_pick(row, "url") or "").strip()
    number_m = _NUMBER.search(str(_pick(row, "number") or "")) or _NUMBER.search(url.rsplit("/", 1)[-1])
    author = _author(_pick(row, "author"))
    number = number_m.group(0) if number_m else ""
    body = str(_pick(row, "body") or "")[:600]
    bits = [f"#{number} {title}" if number else title, f"author: {author}" if author else "",
            f"date: {iso}" if iso else "", url, body]
    text = " | ".join(b for b in bits if b)
    return Record("", PR, text, title=title, author=author, body=body, date=iso, url=url, number=number)


def parse_csv(lines: list[str], today: date) -> tuple[list[Record], int]:
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delimiter_of(lines[0]))
    out, dropped = [], 0
    for row in reader:
        record = from_row(row, today)
        out, dropped = (out + [record], dropped) if record else (out, dropped + 1)
    return out, dropped


def parse_json(text: str, today: date) -> tuple[list[Record], int] | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), None)
    if not isinstance(data, list):
        return None
    rows = [from_row(r, today) if isinstance(r, dict) else None for r in data]
    return [r for r in rows if r], sum(1 for r in rows if r is None)
