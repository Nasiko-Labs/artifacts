"""Pure: column types and the table profile, computed in code (pandas), never by the model.

Order of inference: boolean (two values among yes/no/true/false/y/n/t/f/0/1) → id (all unique and the name
says id) → numeric (≥ 90% of non-null cells parse after stripping $ , %) → datetime (≥ 90% parse) →
category (n_unique ≤ min(50, 20% of rows)) → text. Dates are month-first unless a value has a day > 12 in
the first position.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

import pandas as pd

from fmt import fmt
from load import Loaded

NULL_TOKENS = frozenset({"", "na", "n/a", "null", "none", "nan", "-", "--", "nil"})
BOOL_TOKENS = frozenset({"yes", "no", "true", "false", "y", "n", "t", "f", "0", "1"})
PARSE_SHARE = 0.9
MIXED_MIN_SHARE = 0.1
CATEGORY_MAX = 50
CATEGORY_ROW_SHARE = 0.2
TOP_VALUES = 3
ID_NAME = re.compile(r"(?:^|[\s_\-])id$|^id(?:[\s_\-]|$)|^(?:key|uuid|guid)$", re.IGNORECASE)
_NUM_JUNK = re.compile(r"[\s$€£,%]")
_DAY_FIRST = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}")
DTYPES = ("numeric", "datetime", "category", "boolean", "id", "text")


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str
    nulls_pct: float
    n_unique: int
    low: str = ""
    high: str = ""
    top: tuple[str, ...] = ()
    samples: tuple[str, ...] = ()
    unparsed: int = 0          # non-null cells that did not parse for the chosen numeric/date type

    def to_dict(self) -> dict:
        data = {"name": self.name, "dtype": self.dtype, "nulls_pct": self.nulls_pct, "n_unique": self.n_unique}
        if self.low or self.high:
            data |= {"min": self.low, "max": self.high}
        if self.top:
            data["top_values"] = list(self.top)
        return data


@dataclass(frozen=True)
class Profile:
    rows: int
    cols: int
    columns: tuple[Column, ...]
    duplicate_rows: int = 0
    constant_cols: tuple[str, ...] = ()
    mixed_type_cols: tuple[str, ...] = ()
    unparsed_share: float = 0.0
    month_first_assumed: tuple[str, ...] = ()

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    def of(self, *dtypes: str) -> list[str]:
        return [c.name for c in self.columns if c.dtype in dtypes]

    def counts(self) -> dict[str, int]:
        return {d: len(self.of(d)) for d in DTYPES if self.of(d)}


@dataclass
class Table:
    """The typed table: numeric → float, datetime → datetime64, everything else → stripped text or NaN."""

    frame: pd.DataFrame
    profile: Profile
    notes: list[str] = field(default_factory=list)


def is_null(value: str) -> bool:
    return value.strip().casefold() in NULL_TOKENS


def to_numbers(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.replace(_NUM_JUNK, "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce").astype("float64")


def to_dates(series: pd.Series) -> tuple[pd.Series, bool]:
    """(parsed, month_first_assumed)."""
    text = series.astype("string")
    firsts = text.str.extract(_DAY_FIRST)[0].dropna().astype(int)
    day_first = bool((firsts > 12).any())
    ambiguous = not day_first and not firsts.empty
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(text, errors="coerce", format="ISO8601")
        if parsed.notna().mean() < PARSE_SHARE:
            parsed = pd.to_datetime(text, errors="coerce", format="mixed", dayfirst=day_first)
    return parsed, ambiguous


def build(loaded: Loaded) -> Table:
    raw = pd.DataFrame(list(loaded.rows), columns=list(loaded.header), dtype=object)
    rows = len(raw)
    typed: dict[str, pd.Series] = {}
    columns, mixed, month_first = [], [], []
    unparsed = 0
    for name in loaded.header:
        clean = raw[name].map(lambda v: None if is_null(str(v)) else str(v).strip())
        column, series, flags = _infer(name, clean, rows)
        typed[name], unparsed = series, unparsed + column.unparsed
        columns.append(column)
        mixed += [name] if "mixed" in flags else []
        month_first += [name] if "month_first" in flags else []
    cells = max(1, rows * len(loaded.header))
    constant = tuple(c.name for c in columns if c.n_unique == 1 and rows > 1)
    profile = Profile(rows=rows, cols=len(loaded.header), columns=tuple(columns),
                      duplicate_rows=int(raw.duplicated().sum()) if rows else 0, constant_cols=constant,
                      mixed_type_cols=tuple(mixed), unparsed_share=round(unparsed / cells, 4),
                      month_first_assumed=tuple(month_first))
    return Table(pd.DataFrame(typed), profile, list(loaded.notes))


def _infer(name: str, clean: pd.Series, rows: int) -> tuple[Column, pd.Series, set[str]]:
    present = clean.dropna()
    n = len(present)
    nulls_pct = round(100 * (rows - n) / rows, 1) if rows else 0.0
    n_unique = int(present.nunique())
    samples = tuple(str(v)[:40] for v in present.drop_duplicates().head(5))
    base = {"name": name, "nulls_pct": nulls_pct, "n_unique": n_unique, "samples": samples}
    lowered = set(present.str.casefold().unique()) if n else set()
    if n and n_unique == 2 and lowered <= BOOL_TOKENS:
        return Column(dtype="boolean", top=_top(present), **base), clean, set()
    if n == rows and n_unique == rows and rows > 1 and ID_NAME.search(name):
        return Column(dtype="id", **base), clean, set()
    numbers = to_numbers(present) if n else pd.Series(dtype="float64")
    share = float(numbers.notna().mean()) if n else 0.0
    if n and share >= PARSE_SHARE:
        series = to_numbers(clean)
        return (Column(dtype="numeric", low=_num(numbers.min()), high=_num(numbers.max()),
                       unparsed=int(n - numbers.notna().sum()), **base), series, set())
    flags = {"mixed"} if MIXED_MIN_SHARE <= share < PARSE_SHARE else set()
    if n:
        dates, ambiguous = to_dates(present)
        if dates.notna().mean() >= PARSE_SHARE:
            full, _ = to_dates(clean)
            flags |= {"month_first"} if ambiguous else set()
            return (Column(dtype="datetime", low=str(dates.min().date()), high=str(dates.max().date()),
                           unparsed=int(n - dates.notna().sum()), **base), full, flags - {"mixed"})
    unparsed = int(n - numbers.notna().sum()) if "mixed" in flags else 0
    limit = min(CATEGORY_MAX, max(2, CATEGORY_ROW_SHARE * rows))
    dtype = "category" if n and n_unique <= limit else "text"
    return Column(dtype=dtype, top=_top(present), unparsed=unparsed, **base), clean, flags


def _top(present: pd.Series) -> tuple[str, ...]:
    return tuple(str(v)[:40] for v in present.value_counts().head(TOP_VALUES).index)


def _num(value: float) -> str:
    return fmt(float(value))
