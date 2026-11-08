"""Pure: choosing columns (code decides; an ask that names none of several value columns pauses), the
stored chart spec, and the checks on a model-written title."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from dataparse import Table
from dataset import numeric_columns, x_column

MAX_TITLE_CHARS = 60
_ALL = re.compile(r"\b(?:all|each|every|both)\b", re.IGNORECASE)
_URL = re.compile(r"https?://|www\.|\.[a-z]{2,4}/", re.IGNORECASE)
_INJECTION = re.compile(r"ignore (?:all|any|previous|prior|the)|instruction|system prompt|<<<|>>>|jailbreak",
                        re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Spec:
    title: str
    type_ask: str = ""            # the user's words that may name a chart type ("line chart")
    model_type: str | None = None
    sort: str | None = None       # value | label | None (rule default)
    top_n: int | None = None
    columns_asked: bool = False   # the value column came from a pause

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> Spec | None:
        if not data:
            return None
        top_n = data.get("top_n")
        return Spec(title=str(data.get("title") or ""), type_ask=str(data.get("type_ask") or ""),
                    model_type=data.get("model_type"), sort=data.get("sort"),
                    top_n=int(top_n) if top_n else None, columns_asked=bool(data.get("columns_asked")))


@dataclass(frozen=True)
class Columns:
    x: str
    y: tuple[str, ...]


def choose_columns(table: Table, ask: str, model: dict | None) -> Columns | None:
    """The x and value columns, or None when the ask is ambiguous (>= 2 value columns, none named)."""
    numeric = numeric_columns(table)
    model = model or {}
    x = model.get("x") if model.get("x") in table.columns and model.get("x") not in numeric else x_column(table)
    numeric = tuple(c for c in numeric if c != x)
    if len(numeric) <= 1:
        return Columns(x, numeric)
    if _ALL.search(ask or ""):
        return Columns(x, numeric)
    named = tuple(c for c in numeric if _names(ask, c))
    if named:
        return Columns(x, named)
    proposed = tuple(c for c in model.get("y") or () if c in numeric)
    if proposed and all(_shares_word(ask, c) for c in proposed):
        return Columns(x, proposed)
    return None


def _names(ask: str, column: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(column.casefold())}(?![a-z0-9])", (ask or "").casefold()))


def _shares_word(ask: str, column: str) -> bool:
    words = {w for w in _WORD.findall((ask or "").casefold()) if len(w) > 3}
    wanted = [w for w in _WORD.findall(column.casefold()) if len(w) > 2]
    return any(a.startswith(w) or w.startswith(a) for w in wanted for a in words)


def default_title(x: str, y: tuple[str, ...]) -> str:
    if len(y) == 1:
        return f"{y[0]} by {x}"
    if len(y) == 2:
        return f"{y[0]} and {y[1]} by {x}"
    return f"{', '.join(y[:-1])} and {y[-1]} by {x}"[:MAX_TITLE_CHARS]


def checked_title(proposed: str | None, default: str, allowed_text: str) -> str:
    """The model's title, unless it is too long, carries a link or an injection marker, or has a digit
    that is not in the user's data or request."""
    title = " ".join(str(proposed or "").split())
    if not title or len(title) > MAX_TITLE_CHARS or _URL.search(title) or _INJECTION.search(title):
        return default
    allowed = set(_DIGITS.findall(allowed_text or ""))
    if any(d not in allowed for d in _DIGITS.findall(title)):
        return default
    return title
