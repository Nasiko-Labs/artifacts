"""Pure: every model answer checked in code. Paths must be candidates (or fetched files), symbols must be
skeleton names, reasons may name only things in the listing, the skeletons or the issue, and numbers in a
reason must appear in the issue. Dropped items are counted. Confidence and needs_human are computed here."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_FILE_PICKS = 8
MAX_SYMBOLS_PER_FILE = 3
FALLBACK_REASON = "ranked by search and path signals"
MAX_REASON_WORDS = 30
SHORT_ISSUE_WORDS = 20

_PATHISH = re.compile(r"[\w.-]+/[\w./-]+|[\w-]+\.(?:py|js|ts|tsx|jsx|go|java|rb|rs|cs|php|kt|swift|sql|ya?ml|json|toml)\b")
_IDENTISH = re.compile(r"`([^`]+)`|\b([A-Za-z_]+_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]+)\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


@dataclass
class Dropped:
    paths: int = 0
    symbols: int = 0
    quotes: int = 0
    reasons: int = 0

    @property
    def total(self) -> int:
        return self.paths + self.symbols + self.quotes + self.reasons


@dataclass(frozen=True)
class Known:
    """What a reason may name: listed paths, skeleton names, and the issue text."""

    paths: Collection[str]
    names: Collection[str] = ()
    issue: str = ""

    def text(self) -> str:
        return "\n".join([*self.paths, *self.names, self.issue]).casefold()


def file_picks(data: Any, allowed: Collection[str], dropped: Dropped) -> list[tuple[str, str]]:
    """(path, reason) in the model's order, only allowed paths, no duplicates, at most 8."""
    out: list[tuple[str, str]] = []
    for item in _items(data, "files"):
        path = str(item.get("path") or "").strip().strip("`")
        if path not in allowed:
            dropped.paths += 1
            continue
        if path not in (p for p, _ in out):
            out.append((path, str(item.get("reason") or "")))
    return out[:MAX_FILE_PICKS]


def symbol_picks(data: Any, names_by_path: Mapping[str, Collection[str]],
                 dropped: Dropped) -> list[tuple[str, str, str]]:
    """(path, symbol, reason); the symbol must be a skeleton name of that file."""
    out: list[tuple[str, str, str]] = []
    for entry in _items(data, "files"):
        path = str(entry.get("path") or "").strip().strip("`")
        names = names_by_path.get(path)
        symbols = entry.get("symbols") if isinstance(entry.get("symbols"), list) else []
        kept = 0
        for sym in symbols:
            name = str(sym.get("name") or "").strip().strip("`") if isinstance(sym, dict) else ""
            if names is None or name not in names or kept >= MAX_SYMBOLS_PER_FILE:
                dropped.symbols += 1
                continue
            if (path, name) not in ((p, n) for p, n, _ in out):
                out.append((path, name, str(sym.get("reason") or "")))
                kept += 1
    return out


def location_picks(data: Any, chosen: Collection[tuple[str, str]], dropped: Dropped) -> dict[tuple[str, str], tuple[str, str]]:
    """(path, symbol) → (quote, reason), only for symbols that were sent."""
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for item in _items(data, "locations"):
        key = (str(item.get("path") or "").strip(), str(item.get("symbol") or "").strip())
        if key not in chosen:
            dropped.symbols += 1
            continue
        out.setdefault(key, (str(item.get("quote") or ""), str(item.get("reason") or "")))
    return out


def reason(text: str, known: Known, dropped: Dropped) -> str:
    """The model's reason if everything it names is known and its numbers are in the issue, else the fallback."""
    words = " ".join(str(text or "").split()).split(" ")
    clean = " ".join(words[:MAX_REASON_WORDS]).strip()
    if not clean:
        return FALLBACK_REASON
    haystack = known.text()
    named = [m for m in _PATHISH.findall(clean)] + [a or b for a, b in _IDENTISH.findall(clean)]
    numbers = _NUMBER.findall(clean)
    if any(n.strip("`").casefold() not in haystack for n in named) or \
            any(n not in known.issue for n in numbers):
        dropped.reasons += 1
        return FALLBACK_REASON
    return clean


def confidence(top_bucket: str | None, top_precision: str | None, searched: bool, repaired: bool,
               issue_words: int, pasted_only_files: int | None) -> str:
    if top_bucket is None or top_bucket == "low" or issue_words < SHORT_ISSUE_WORDS:
        return "low"
    if pasted_only_files is not None and pasted_only_files < 2:
        return "low"
    if top_bucket == "high" and top_precision == "line" and searched and not repaired:
        return "high"
    if top_precision is None:
        return "low" if top_bucket != "high" else "medium"
    return "medium"


def needs_human(conf: str, needs_info_flag: bool, kind: str) -> bool:
    return conf == "low" or needs_info_flag or kind == "unclear"


def _items(data: Any, key: str) -> Sequence[dict]:
    raw = data.get(key) if isinstance(data, dict) else None
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
