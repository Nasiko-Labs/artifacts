"""Pure: the pasted change list -> Change records, then dedupe. Formats, first match wins:

1. JSON: an envelope `{"items": [...]}` or an export array of objects (title/number/labels/url/body).
2. CSV export with a header row naming a title column (case-insensitive aliases).
3. Lines: conventional commits (`feat(export)!: ... (#412)`, an optional `git log --oneline` hash),
   or bulleted / plain lines with `ABC-123` issue keys and `[label]` tags. An indented line continues the
   previous change; a `BREAKING CHANGE:` line marks it breaking and carries the migration words.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from change import Change

TITLE_KEYS = ("title", "summary", "name", "subject", "pr title", "issue title", "description")
REF_KEYS = ("number", "id", "key", "pr", "#", "issue", "pr number", "issue key")
URL_KEYS = ("url", "link", "html_url", "web_url")
BODY_KEYS = ("body", "details", "notes", "description")
LABEL_KEYS = ("labels", "label", "tags", "type")

_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_HASH = re.compile(r"^[0-9a-f]{7,40}\s+")
_BULLET = re.compile(r"^(?:[-*+•]|\d{1,3}[.)])\s+")
_CONVENTIONAL = re.compile(r"^(?P<type>[A-Za-z]{2,12})(?:\((?P<scope>[^)]{1,40})\))?(?P<bang>!)?:\s*(?P<desc>\S.*)$")
_ISSUE_KEY = re.compile(r"^(?P<key>[A-Z][A-Z0-9]{1,9}-\d{1,6})\b[:\s-]*")
_LABEL = re.compile(r"\[([^\]\n]{1,40})\]")
_URL = re.compile(r"https?://[^\s)>\]]+")
_PR_NUMBER = re.compile(r"\s*\(?#(\d{1,7})\)?\s*$")
_BREAKING = re.compile(r"^\s*BREAKING[ -]CHANGES?\s*:\s*(.*)$", re.IGNORECASE)
_MERGE = re.compile(r"^merge (?:pull request|branch|remote-tracking)\b", re.IGNORECASE)


def parse(text: str) -> list[Change]:
    """All changes found in `text`, in input order, deduplicated, ids c1..cN."""
    raw = _from_json(text)
    if raw is None:
        raw = _from_csv(text)
    if raw is None:
        raw = _from_lines(text)
    return [replace(c, id=f"c{n}") for n, c in enumerate(dedupe(raw), start=1)]


def dedupe(changes: Iterable[Change]) -> list[Change]:
    seen: set[str] = set()
    kept: list[Change] = []
    for change in changes:
        keys = {f"t:{_norm(change.title)}"} | ({f"r:{change.ref.casefold()}"} if change.ref else set())
        if keys & seen or not change.title:
            continue
        seen |= keys
        kept.append(change)
    return kept


def _norm(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", title.casefold()).split())


def _from_json(text: str) -> list[Change] | None:
    body = (text or "").strip()
    fenced = _FENCED.match(body)
    body = fenced.group(1) if fenced else body
    if not body.startswith(("{", "[")):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    rows = data.get("items") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    return [c for c in (_from_row(r) for r in rows if isinstance(r, Mapping)) if c is not None]


def _from_csv(text: str) -> list[Change] | None:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2 or "," not in lines[0]:
        return None
    headers = [h.strip().casefold() for h in next(csv.reader([lines[0]]))]
    if not any(h in TITLE_KEYS for h in headers):
        return None
    rows = csv.DictReader(io.StringIO("\n".join(lines)), fieldnames=headers)
    next(rows)  # the header row itself
    return [c for c in (_from_row(r) for r in rows) if c is not None]


def _pick(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(k).strip().casefold(): v for k, v in row.items() if k is not None}
    for key in keys:
        value = lowered.get(key)
        if value not in (None, "", []):
            return value
    return None


def _from_row(row: Mapping[str, Any]) -> Change | None:
    title = _pick(row, TITLE_KEYS)
    if not isinstance(title, str) or not title.strip():
        return None
    labels = _pick(row, LABEL_KEYS) or []
    if isinstance(labels, str):
        labels = [x for x in re.split(r"[;,|]", labels)]
    names = [str(x.get("name") if isinstance(x, Mapping) else x).strip() for x in labels]
    ref = _pick(row, REF_KEYS)
    ref_text = str(ref).strip() if ref is not None else ""
    if ref_text.isdigit():
        ref_text = f"#{ref_text}"
    body = _pick(row, BODY_KEYS)
    body_text = str(body).strip() if body and body != title else ""
    url = _pick(row, URL_KEYS)
    line = _from_line(title.strip())
    if line is None:
        return None
    return replace(line, ref=line.ref or ref_text, body=" ".join(body_text.split()),
                   url=str(url).strip() if url else line.url,
                   labels=tuple(dict.fromkeys([*line.labels, *(n for n in names if n)])))


def _from_lines(text: str) -> list[Change]:
    changes: list[Change] = []
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        breaking = _BREAKING.match(raw)
        if breaking and changes:
            words = breaking.group(1).strip()
            last = changes[-1]
            changes[-1] = replace(last, breaking=True, migration=" ".join(f"{last.migration} {words}".split()))
            continue
        if raw[:1] in (" ", "\t") and changes and not _BULLET.match(raw.strip()):
            last = changes[-1]
            changes[-1] = replace(last, body=" ".join(f"{last.body} {raw.strip()}".split()))
            continue
        change = _from_line(raw.strip())
        if change is not None:
            changes.append(change)
    return changes


def _from_line(line: str) -> Change | None:
    text = _BULLET.sub("", _HASH.sub("", line.strip())).strip()
    url = _URL.search(text)
    text = _URL.sub("", text).strip()
    labels = tuple(m.strip() for m in _LABEL.findall(text))
    text = _LABEL.sub("", text).strip()
    prefix = scope = ""
    bang = False
    conventional = _CONVENTIONAL.match(text)
    if conventional:
        prefix, scope = conventional.group("type").casefold(), (conventional.group("scope") or "").strip()
        bang, text = bool(conventional.group("bang")), conventional.group("desc").strip()
    elif _MERGE.match(text):
        prefix = "merge"
    key = _ISSUE_KEY.match(text)
    ref = key.group("key") if key else ""
    text = text[key.end():].strip() if key else text
    number = _PR_NUMBER.search(text)
    if number:
        ref = ref or f"#{number.group(1)}"
        text = text[:number.start()].strip()
    title = " ".join(text.strip(" -:").split())
    if not title:
        return None
    breaking = bang or any(lb.casefold() == "breaking" for lb in labels)
    return Change(id="", title=title, ref=ref, url=url.group(0) if url else "", labels=labels, prefix=prefix,
                  scope=scope, breaking=breaking)
