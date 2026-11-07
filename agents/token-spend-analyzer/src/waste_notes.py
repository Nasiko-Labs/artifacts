"""Pure: waste rules T05–T08, text findings without an estimate: output-heavy groups, runaway entities and spike
days, a long tail of models, and unpriced or estimated cost."""

from __future__ import annotations

import fmt
import prices
from frame import Frame
from units import units

T05_MIN_OUT_IN_RATIO = 1.5
T05_MIN_OUTPUT_COST_PCT = 60.0
T06_RUNAWAY_PCT = 50.0
T06_SPIKE_MULTIPLE = 3.0
T06_MIN_DAYS = 3
T07_MIN_MODELS = 6
T07_SMALL_SHARE_PCT = 2.0
T08_ESTIMATED_PCT = 10.0


def _note(rule: str, title: str, dimension: str, key: str, evidence: str, assumption: str, hint: str,
          extra: dict | None = None) -> dict:
    return {"rule_id": rule, "title": title, "dimension": dimension, "key": key, "evidence": evidence,
            "est_in_export": None, "est_monthly_waste": None, "assumption": assumption, "hint": hint, **(extra or {})}


def t05_output_heavy(frame: Frame) -> list[dict]:
    out = []
    for u in units(frame):
        p = u["price"]
        if p is None or not u["input"] or u["output"] / u["input"] < T05_MIN_OUT_IN_RATIO:
            continue
        o = float(prices.output_cost(p, u["output"]))
        share = o / (o + float(prices.input_cost(p, u["input"], u["cached"]))) * 100
        if share >= T05_MIN_OUTPUT_COST_PCT:
            out.append(_note("T05", "output-heavy", u["dimension"], u["key"],
                             f"{u['label']} writes {fmt.ratio(u['output'] / u['input'])}x its input; output is "
                             f"{fmt.pct(share)} of its cost", "assumes shorter answers are acceptable",
                             "Cap max_tokens and ask for shorter answers."))
    return out


def t06_runaway(frame: Frame) -> list[dict]:
    df, out = frame.df, []
    total = float(df["cost"].sum())
    for dim in ("team", "task"):
        if dim not in frame.dims or not total:
            continue
        sums = df.groupby(dim)["cost"].sum().sort_values(ascending=False)
        key, cost = str(sums.index[0]), float(sums.iloc[0])
        share = cost / total * 100
        if share > T06_RUNAWAY_PCT and len(sums) > 1:
            label = "agent" if frame.shape == "nasiko" else dim
            out.append(_note("T06", "runaway entity", label, key,
                             f"{label} `{key}` is {fmt.pct(share)} of cost ({fmt.money(cost)})",
                             "concentration is not waste by itself; check it is expected",
                             f"Review what drives {label} {key} and set it a budget.", {"share_pct": share}))
    return out + _spike(frame)


def _spike(frame: Frame) -> list[dict]:
    daily = frame.df.dropna(subset=["ts"]).groupby("ts")["cost"].sum()
    if len(daily) < T06_MIN_DAYS:
        return []
    median = float(daily.median())
    day, value = daily.idxmax(), float(daily.max())
    if not median or value <= T06_SPIKE_MULTIPLE * median:
        return []
    date = day.strftime("%Y-%m-%d")
    return [_note("T06", "spike day", "date", date,
                  f"{date} cost {fmt.money(value)}, {fmt.ratio(value / median)}x the daily median of "
                  f"{fmt.money(median)}", "a one-off may be legitimate (a backfill or batch run)",
                  f"Check what ran on {date}.")]


def t07_long_tail(frame: Frame) -> list[dict]:
    df = frame.df
    total = float(df["cost"].sum())
    if "model" not in frame.dims or not total:
        return []
    shares = df.groupby("model")["cost"].sum() / total * 100
    small = sorted(str(m) for m, s in shares.items() if s < T07_SMALL_SHARE_PCT)
    if len(small) < T07_MIN_MODELS:
        return []
    return [_note("T07", "long tail of models", "model", ", ".join(small),
                  f"{len(small)} models each under {fmt.pct(T07_SMALL_SHARE_PCT)} of cost: {', '.join(small)}",
                  "consolidating assumes the remaining models cover these uses",
                  "Consolidate rarely used models to simplify pricing and evaluation.")]


def t08_unpriced(frame: Frame) -> list[dict]:
    df, meta, out = frame.df, frame.meta, []
    unknown = df["cost_basis"] == "unknown"
    if unknown.any():
        models = sorted({str(m) for m in df.loc[unknown, "model"].dropna()}) if "model" in frame.dims else []
        out.append(_note("T08", "unpriced usage", "rows", "unknown cost",
                         f"{int(unknown.sum()):,} rows ({fmt.count(df.loc[unknown, 'requests'].sum())} requests) have "
                         "no cost and no known price" + (f" (models: {', '.join(models[:5])})" if models else ""),
                         "figures under-report by about the unpriced share",
                         "Add the missing models to a price_table line, e.g. '<model> input $x output $y per 1M'.",
                         {"rows": int(unknown.sum())}))
    calls = int(meta.get("unpriced_calls") or 0)
    if calls:
        out.append(_note("T08", "unpriced calls", "summary", "unpriced_calls",
                         f"{calls:,} calls in the export carry no cost (no price row for their model)",
                         "figures under-report by about the unpriced share",
                         "Add price rows for the unpriced models in the platform's pricing.", {"calls": calls}))
    est, total = float(meta.get("estimated_cost") or 0.0), float(meta.get("summary_total_cost") or 0.0)
    if total and est / total * 100 > T08_ESTIMATED_PCT:
        out.append(_note("T08", "estimated cost", "summary", "estimated_cost",
                         f"{fmt.money(est)} of {fmt.money(total)} ({fmt.pct(est / total * 100)}) is priced from "
                         "inferred rates", "estimated figures may differ from the invoice",
                         "Confirm the inferred rates against the provider invoice."))
    return out


def all_notes(frame: Frame) -> list[dict]:
    return t05_output_heavy(frame) + t06_runaway(frame) + t07_long_tail(frame) + t08_unpriced(frame)
