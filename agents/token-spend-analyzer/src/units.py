"""Pure: the unit groups the waste rules read: per task × model (or whichever of those the export has, else per
team), with sums, per-request averages and the model's price when the price table knows it."""

from __future__ import annotations

import prices
from frame import Frame


def unit_keys(frame: Frame) -> list[str]:
    keys = [d for d in ("task", "model") if d in frame.dims]
    return keys or (["team"] if "team" in frame.dims else [])


def units(frame: Frame) -> list[dict]:
    """Per task × model (or what of those exists): sums, averages and the model's price when known."""
    keys = unit_keys(frame)
    if not keys:
        return []
    out = []
    for key, g in frame.df.groupby(keys, sort=False):
        key = key if isinstance(key, tuple) else (key,)
        named = dict(zip(keys, (str(k) for k in key)))
        req = float(g["requests"].sum()) or 1.0
        model = named.get("model")
        ordered = ([model] if model else []) + [named[k] for k in keys if k != "model"]
        out.append({"label": " on ".join(f"`{v}`" for v in ordered),
                    "dimension": " × ".join(keys), "key": " / ".join(named.values()), "model": model,
                    "price": prices.lookup(model, prices.table_with(frame.meta.get("price_table"))) if model else None,
                    "requests": req, "input": float(g["input"].sum()), "output": float(g["output"].sum()),
                    "cached": float(g["cached_in"].sum()), "cost": float(g["cost"].sum()),
                    "avg_in": float(g["input"].sum()) / req, "avg_out": float(g["output"].sum()) / req})
    return out
