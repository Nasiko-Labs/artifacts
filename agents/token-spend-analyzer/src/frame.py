"""Pure: normalise a loaded export to one frame with fixed columns

    ts, model, team, task, user, input, output, cached_in, cache_write, requests, cost, cost_basis, failed

`input` always includes cached reads and cache writes. `cost_basis` is reported (from the export), computed (from
the price table) or unknown (no price for the model). `failed` is 1/0, or NaN when the export has neither a status
nor an attempt column. Negative costs and rows with no readable token or cost value are dropped and counted."""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import columns
import prices
from detect import Loaded

DIMS = ("model", "team", "task", "user")
NUMS = ("input", "output", "cached_in", "cache_write", "requests", "cost")
CANONICAL = ("ts", *DIMS, *NUMS, "cost_basis", "failed")
UNKNOWN_KEY = "unknown"
SUCCESS = frozenset({"", "success", "succeeded", "ok", "200", "completed", "complete", "true", "1", "pass", "passed"})
NO_ERROR = frozenset({"", "none", "null", "false", "0", "nan"})
EPOCH_SECONDS_FROM = 1e9


@dataclass(frozen=True)
class Frame:
    df: pd.DataFrame
    shape: str
    rows_read: int
    dropped: dict = field(default_factory=dict)       # reason → count
    capped: bool = False
    meta: dict = field(default_factory=dict)
    dims: tuple[str, ...] = ()
    has_status: bool = False

    @property
    def rows_dropped(self) -> int:
        return int(sum(self.dropped.values()))


def numbers(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.replace(r"[$€£,\s]", "", regex=True).replace("", np.nan)
    return pd.to_numeric(cleaned, errors="coerce")


def timestamps(series: pd.Series) -> pd.Series:
    as_num = pd.to_numeric(series, errors="coerce")
    if as_num.notna().all() and len(as_num) and (as_num > EPOCH_SECONDS_FROM).all():
        parsed = pd.to_datetime(as_num, unit="s", errors="coerce", utc=True)
    else:
        parsed = pd.to_datetime(series.astype(str), errors="coerce", utc=True, format="mixed")
    return parsed.dt.tz_localize(None).dt.normalize()


def _failed(raw: pd.DataFrame, cols: columns.ColumnMap) -> pd.Series | None:
    flags = None
    if cols.has("status"):
        header = cols.roles["status"]
        values = raw[header].astype(str).str.strip().str.casefold()
        kind = columns.norm(header)
        if kind in ("error", "error_type"):
            flags = ~values.isin(NO_ERROR)
        elif kind == "success":
            flags = values.isin({"false", "0", "no", "failed", "failure"})
        else:
            flags = ~values.isin(SUCCESS)
    if cols.has("attempt"):
        retried = numbers(raw[cols.roles["attempt"]]).fillna(0)
        retried = retried > (0 if columns.norm(cols.roles["attempt"]).startswith("retr") else 1)
        flags = retried if flags is None else flags | retried
    return None if flags is None else flags.astype(float)


def normalise(loaded: Loaded, table: Mapping[str, prices.Price] | None = None) -> Frame:
    raw, cols = loaded.df, loaded.cols
    out = pd.DataFrame(index=raw.index)
    if loaded.shape == "nasiko":
        for name in NUMS:
            out[name] = pd.to_numeric(raw[name], errors="coerce")
        out["team"] = raw["team"].astype(str)
    else:
        for name in NUMS:
            out[name] = numbers(raw[cols.roles[name]]) if cols.has(name) else np.nan
        for name in DIMS:
            if cols.has(name):
                out[name] = raw[cols.roles[name]].astype(str).str.strip().replace("", UNKNOWN_KEY)
        if cols.has("ts"):
            out["ts"] = timestamps(raw[cols.roles["ts"]])
    if cols.input_excludes_cache:
        out["input"] = out["input"].fillna(0) + out["cached_in"].fillna(0) + out["cache_write"].fillna(0)
    failed = _failed(raw, cols) if loaded.shape != "nasiko" else None
    out["failed"] = failed if failed is not None else np.nan
    return _finish(out, loaded, table, has_status=failed is not None)


def _finish(out: pd.DataFrame, loaded: Loaded, table, has_status: bool) -> Frame:
    rows_read = len(out)
    negative = out["cost"] < 0
    unreadable = out[["input", "output", "cost"]].isna().all(axis=1)
    dropped = {k: int(v) for k, v in (("negative cost", negative.sum()), ("no readable tokens or cost",
                                                                          (unreadable & ~negative).sum())) if v}
    out = out[~negative & ~unreadable].copy()
    for name in ("input", "output", "cached_in", "cache_write"):
        out[name] = out[name].fillna(0.0)
    out["requests"] = out["requests"].fillna(1.0)
    out = _priced(out, prices.table_with(table))
    dims = tuple(d for d in DIMS if d in out.columns)
    for name in CANONICAL:
        if name not in out.columns:
            out[name] = pd.NaT if name == "ts" else (np.nan if name in NUMS or name == "failed" else None)
    return Frame(out[list(CANONICAL)].reset_index(drop=True), loaded.shape, rows_read, dropped, loaded.capped,
                 {**loaded.meta, "price_table": dict(table or {})}, dims, has_status)


def _priced(out: pd.DataFrame, table: Mapping[str, prices.Price]) -> pd.DataFrame:
    out = out.assign(cost_basis=np.where(out["cost"].notna(), "reported", "unknown"))
    if "model" not in out.columns:
        return out
    missing = out["cost"].isna()
    for model in out.loc[missing, "model"].unique():
        price = prices.lookup(model, table)
        if price is None:
            continue
        rows = missing & (out["model"] == model)
        part = out.loc[rows]
        out.loc[rows, "cost"] = prices.token_cost(price, part["input"], part["output"], part["cached_in"],
                                                  part["cache_write"])
        out.loc[rows, "cost_basis"] = "computed"
    return out


def to_canonical_csv(frame: Frame) -> str:
    df = frame.df.copy()
    df["ts"] = df["ts"].dt.strftime("%Y-%m-%d")
    return df.to_csv(index=False)


def from_canonical_csv(text: str, shape: str, meta: dict, dims: tuple[str, ...], has_status: bool,
                       rows_read: int, dropped: dict) -> Frame:
    df = pd.read_csv(io.StringIO(text), dtype={d: str for d in DIMS} | {"cost_basis": str})
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    for d in DIMS:
        df[d] = df[d].where(df[d].notna(), None) if d in dims else None
    return Frame(df, shape, rows_read, dict(dropped), False, dict(meta), tuple(dims), has_status)
