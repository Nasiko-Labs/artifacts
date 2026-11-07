"""Pure: detect the export's shape and read it. Shapes: nasiko (FinOps dashboard JSON), openai, anthropic,
gateway (LiteLLM-style JSON/JSONL or CSV), generic. A missing required role (cost or tokens), or a missing model
column while unmapped text columns exist, is reported as `missing` with candidate headers for the question."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

import columns
import parse_csv
import parse_logs
import parse_nasiko

NONE_OPTION = ""        # an override of "" means "this export has no such column"
MAX_CANDIDATES = 8
NUMERIC_SAMPLE = 20


@dataclass(frozen=True)
class Loaded:
    shape: str = ""
    df: pd.DataFrame | None = None
    cols: columns.ColumnMap = field(default_factory=columns.ColumnMap)
    meta: dict = field(default_factory=dict)
    capped: bool = False
    error: str = ""
    missing: str = ""                       # the role to ask about, if any
    candidates: tuple[str, ...] = ()


def parse_json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return parse_logs.parse_jsonl(text)


def load(source: str, kind: str, overrides: Mapping[str, str] | None = None) -> Loaded:
    overrides = dict(overrides or {})
    if kind == "json":
        obj = parse_json(source)
        if parse_nasiko.is_nasiko(obj):
            df, meta = parse_nasiko.read(obj)
            cols = columns.resolve(df.columns)
            return Loaded("nasiko", df, cols, meta)
        records = parse_logs.records_of(obj)
        if not records:
            return Loaded(error="json")
        df, capped = parse_logs.read(records)
        return _mapped("gateway", df, capped, overrides)
    try:
        df, capped = parse_csv.read(source)
    except ValueError:
        return Loaded(error="csv")
    shape = parse_csv.shape_of({columns.norm(c) for c in df.columns})
    return _mapped(shape, df, capped, overrides)


def _mapped(shape: str, df: pd.DataFrame, capped: bool, overrides: dict) -> Loaded:
    cols = columns.resolve(df.columns, overrides)
    unmapped = [c for c in df.columns if c not in cols.roles.values()]
    if not any(cols.has(r) for r in ("cost", "input", "output")):
        cands = tuple(c for c in unmapped if _numeric(df[c]))[:MAX_CANDIDATES]
        return Loaded(shape, df, cols, capped=capped, missing="cost", candidates=cands)
    if not cols.has("model") and "model" not in overrides:
        cands = tuple(c for c in unmapped if not _numeric(df[c]) and df[c].astype(str).str.strip().ne("").any())
        if cands:
            return Loaded(shape, df, cols, capped=capped, missing="model", candidates=cands[:MAX_CANDIDATES])
    return Loaded(shape, df, cols, capped=capped)


def _numeric(series: pd.Series) -> bool:
    sample = series.astype(str).str.strip().replace("", pd.NA).dropna().head(NUMERIC_SAMPLE)
    if sample.empty:
        return False
    cleaned = sample.str.replace(r"[$,€£\s]", "", regex=True)
    return bool(pd.to_numeric(cleaned, errors="coerce").notna().all())
