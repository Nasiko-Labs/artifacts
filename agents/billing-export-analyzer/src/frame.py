"""The pasted CSV as a pandas frame with canonical columns: `date`, `service`, `cost`, `credit` (bool) and
whichever of `account`, `region`, `usage_type`, `line_type`, `tag:<key>` the export has.

Money: currency symbols and thousands separators stripped, `(12.30)` and `-12.30` are negative, empty → 0
(counted). Credits: a line type of Credit/Refund/Tax/SavingsPlanNegation, or any negative cost, is kept out
of gross usage. Dates: `pandas.to_datetime(errors="coerce")`; unparseable rows are counted and dropped."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

import pandas as pd

from columns import Columns

MAX_ROWS = 200_000
PASTE_LIMIT_CHARS = 100_000
NEAR_LIMIT_CHARS = 99_000
CREDIT_TYPES = ("credit", "refund", "tax", "savingsplannegation")
DIMENSIONS = ("account", "region", "usage_type", "line_type")
_MONEY_JUNK = re.compile(r"[^\d.\-]")
_PAREN = re.compile(r"^\((.*)\)$")


@dataclass(frozen=True)
class Frame:
    df: pd.DataFrame
    rows_read: int
    rows_dropped: int
    empty_costs: int
    currency: str | None
    notes: tuple[str, ...] = ()
    cut: bool = False            # rows or characters were cut (200k rows, the paste limit)

    @property
    def tag_keys(self) -> tuple[str, ...]:
        return tuple(c[4:] for c in self.df.columns if c.startswith("tag:"))

    def dims(self) -> tuple[str, ...]:
        return tuple(c for c in (*DIMENSIONS, *(f"tag:{k}" for k in self.tag_keys)) if c in self.df.columns)


def delimiter_of(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (",", "\t", ";", "|")}
    return max(counts, key=counts.get)


def headers_of(text: str) -> list[str]:
    first = (text or "").lstrip("﻿").splitlines()[0] if (text or "").strip() else ""
    row = next(csv.reader([first], delimiter=delimiter_of(first)), [])
    return [h.strip() for h in row]


def parse_money(value: object) -> float | None:
    """A float, or None for an empty cell."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    paren = _PAREN.match(text)
    negative = bool(paren) or text.startswith(("-", "−"))
    digits = _MONEY_JUNK.sub("", (paren.group(1) if paren else text).replace("−", "-")).lstrip("-")
    try:
        amount = float(digits)
    except ValueError:
        return None
    return -amount if negative else amount


def _cut_partial_row(text: str, delimiter: str) -> tuple[str, bool]:
    """Drop a last line that has fewer fields than the header (a paste cut mid-row)."""
    lines = text.rstrip("\n").split("\n")
    if len(lines) < 3:
        return text, False
    width = len(next(csv.reader([lines[0]], delimiter=delimiter)))
    last = next(csv.reader([lines[-1]], delimiter=delimiter), [])
    if len(last) < width:
        return "\n".join(lines[:-1]), True
    return text, False


def load(text: str, columns: Columns) -> Frame:
    clean = (text or "").lstrip("﻿").replace("\r\n", "\n")
    delimiter = delimiter_of(clean.split("\n", 1)[0])
    clean, partial = _cut_partial_row(clean, delimiter)
    notes: list[str] = []
    raw = pd.read_csv(io.StringIO(clean), sep=delimiter, dtype=str, keep_default_na=False, nrows=MAX_ROWS + 1,
                      skipinitialspace=True, on_bad_lines="skip")
    raw.columns = [str(c).strip() for c in raw.columns]
    cut = partial or len(text or "") >= NEAR_LIMIT_CHARS
    if len(raw) > MAX_ROWS:
        raw = raw.iloc[:MAX_ROWS]
        cut = True
        notes.append(f"The export has more than {MAX_ROWS:,} rows; I read the first {MAX_ROWS:,}.")
    if partial:
        notes.append("The last row looked cut off (the paste may have hit the chat's 100,000-character limit), "
                     "so I dropped it.")
    if len(text or "") >= NEAR_LIMIT_CHARS and not partial:
        notes.append("Your paste is at the chat's 100,000-character limit, so the export may be cut. Pre-aggregate "
                     "it (daily by service) or send it as a file.")
    return _canonical(raw, columns, notes, cut)


def _canonical(raw: pd.DataFrame, columns: Columns, notes: list[str], cut: bool) -> Frame:
    rows_read = len(raw)
    costs = raw[columns.cost].map(parse_money)
    empty = int(costs.isna().sum())
    df = pd.DataFrame({
        "date": pd.to_datetime(raw[columns.date].str.strip(), errors="coerce", format="mixed", utc=True).dt.tz_localize(None)
        if len(raw) else pd.Series(dtype="datetime64[ns]"),
        "service": raw[columns.service].str.strip().replace("", "(unknown service)"),
        "cost": costs.fillna(0.0).astype(float),
    })
    for role in DIMENSIONS:
        header = getattr(columns, role)
        if header:
            df[role] = raw[header].str.strip()
    for key, header in columns.tags.items():
        df[f"tag:{key}"] = raw[header].str.strip()
    df["date"] = df["date"].dt.normalize()
    bad_dates = int(df["date"].isna().sum())
    df = df[df["date"].notna()].reset_index(drop=True)
    line = df["line_type"].str.casefold().str.replace(r"[\s_]", "", regex=True) if "line_type" in df else None
    credit = df["cost"] < 0
    if line is not None:
        credit = credit | line.isin(CREDIT_TYPES)
    df["credit"] = credit
    if bad_dates:
        notes.append(f"{bad_dates:,} row{'s' if bad_dates != 1 else ''} dropped: unparseable date.")
    if empty:
        notes.append(f"{empty:,} empty cost cell{'s' if empty != 1 else ''} counted as 0.")
    currency = None
    if columns.currency:
        values = raw[columns.currency].str.strip().replace("", pd.NA).dropna().unique()
        currency = str(values[0]) if len(values) == 1 else None
    return Frame(df=df, rows_read=rows_read, rows_dropped=bad_dates, empty_costs=empty, currency=currency,
                 notes=tuple(notes), cut=cut)


def to_canonical_csv(frame: Frame) -> str:
    """The frame, aggregated by every column but cost, as CSV with canonical headers (for follow-ups)."""
    df = frame.df.copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df["line_type"] = df.get("line_type", pd.Series([""] * len(df))).where(~df["credit"], "Credit")
    keys = [c for c in df.columns if c not in ("cost", "credit")]
    grouped = df.fillna("").groupby(keys, sort=False, dropna=False)["cost"].sum().reset_index()
    grouped["cost"] = grouped["cost"].round(6)
    renamed = grouped.rename(columns={c: f"tag_{c[4:]}" for c in grouped.columns if c.startswith("tag:")})
    return renamed.to_csv(index=False)
