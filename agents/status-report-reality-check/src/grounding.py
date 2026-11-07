"""Pure: the model's report items, checked against the report itself.

- The snippet must appear in the report (whitespace, case and quote style aside), else the item is dropped.
- Status words, owner and ticket refs are kept only when they appear in the report; export ids that appear
  in the snippet are added as refs by code.
- `reported` is re-derived from the status words with the code table; the model's own category is used only
  when the words map to nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import statuses

MAX_ITEM_WORDS = 12
MAX_SNIPPET_CHARS = 200
HEADLINE_COUNT = 3
VALID_REPORTED = frozenset({statuses.DONE, statuses.ON_TRACK, statuses.AT_RISK, statuses.BLOCKED,
                            statuses.NOT_STARTED, statuses.UNKNOWN})

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
_SPACES = re.compile(r"\s+")
_MARKDOWN = re.compile(r"[*_`]+")
_HEADLINE = re.compile(r"\b(?:critical|top priority|priority|p0|headline)\b", re.IGNORECASE)


def norm(text: str) -> str:
    return _SPACES.sub(" ", _MARKDOWN.sub("", (text or "").translate(_QUOTES)).casefold()).strip()


def appears(fragment: str, text: str) -> bool:
    needle = norm(fragment)
    return bool(needle) and needle in norm(text)


def mentions(ref: str, text: str) -> bool:
    """`ref` appears in `text` as a whole token (KEY-1 does not match KEY-12)."""
    needle = norm(ref)
    if not needle:
        return False
    return re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-]|-\d)", norm(text)) is not None


def ground(raw_items: Iterable[dict], report: str, export_ids: Iterable[str]) -> tuple[list[dict], int]:
    """(checked items numbered r1.., how many were dropped)."""
    ids = list(export_ids)
    kept: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    for raw in raw_items:
        item = _checked(raw, report, ids)
        if item is None or norm(item["item"]) in seen:
            dropped += item is None
            continue
        seen.add(norm(item["item"]))
        kept.append(item)
    numbered = [{**item, "id": f"r{n}", "headline": n <= HEADLINE_COUNT or bool(_HEADLINE.search(item["snippet"]))}
                for n, item in enumerate(kept, start=1)]
    return numbered, dropped


def _checked(raw: dict, report: str, export_ids: list[str]) -> dict | None:
    name = " ".join(_text(raw.get("item")).split()[:MAX_ITEM_WORDS])
    snippet = _text(raw.get("snippet"))[:MAX_SNIPPET_CHARS]
    if not name or not snippet or not appears(snippet, report):
        return None
    words = _text(raw.get("reported_words"))
    words = words if appears(words, report) else ""
    reported = statuses.reported_category(words)
    if reported == statuses.UNKNOWN:
        model_value = _text(raw.get("reported")).casefold()
        reported = model_value if model_value in VALID_REPORTED else statuses.UNKNOWN
    refs = [r for r in (_text(x) for x in raw.get("refs") or () if isinstance(x, str)) if mentions(r, report)]
    refs += [i for i in export_ids if mentions(i, snippet) and not any(norm(i) == norm(r) for r in refs)]
    owner = _text(raw.get("owner"))
    due = _text(raw.get("due"))
    return {"item": name, "reported": reported, "reported_words": words, "refs": refs,
            "owner": owner if owner and appears(owner, report) else None,
            "due": due if due and appears(due, report) else None, "snippet": snippet,
            "match": {"kind": "none", "ids": []}}


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""
