"""Pure: the numeric analyses of the closed menu (pandas). Each takes the typed table, the validated
columns and params, and returns a Result whose facts carry exact formatted numbers. No model output is
ever executed: the menu names these functions, code calls them."""

from __future__ import annotations

import pandas as pd

from fmt import fmt, pct
from profiling import Table
from result import Fact, Result, rows

SMALL_GROUP = 5
CORR_MIN_N = 30
CORR_MIN_R = 0.3
OUTLIER_K = 1.5
COMPOSITION_TOP = 8
EXTREME_IDS = 5


def summary_stats(table: Table, cols: dict, params: dict) -> Result:
    name = cols["value"]
    s = table.frame[name].dropna()
    if s.empty:
        return Result("summary_stats", cols, f"Summary of {name}", facts=(Fact(f"{name} has no numeric values."),))
    stats = [("count", len(s)), ("sum", s.sum()), ("mean", s.mean()), ("median", s.median()), ("min", s.min()),
             ("max", s.max()), ("p10", s.quantile(0.1)), ("p90", s.quantile(0.9)),
             ("std", s.std() if len(s) > 1 else 0.0)]
    v = {k: fmt(x) for k, x in stats}
    fact = Fact(f"{name} has {v['count']} values totalling {v['sum']}; mean {v['mean']}, median {v['median']}, "
                f"ranging from {v['min']} to {v['max']}.",
                (v["count"], v["sum"], v["mean"], v["median"], v["min"], v["max"]))
    spread = Fact(f"Most {name} values (p10 to p90) lie between {v['p10']} and {v['p90']}; standard deviation "
                  f"{v['std']}.", (v["p10"], v["p90"], v["std"]))
    return Result("summary_stats", cols, f"Summary of {name}", ("statistic", name), rows(list(v.items())),
                  (fact, spread))


def _grouped(table: Table, group: str, value: str | None, agg: str) -> pd.Series:
    frame = table.frame
    if agg == "count" or not value:
        return frame.groupby(group).size().astype(float).sort_values(ascending=False)
    series = frame.groupby(group)[value].agg(agg).dropna()
    return series.astype(float).sort_values(ascending=False)


CHART_BARS = 10


def top_n(table: Table, cols: dict, params: dict) -> Result:
    group, value, agg, n = cols["group"], cols.get("value"), params.get("agg", "sum"), params.get("n", 10)
    series = _grouped(table, group, value, agg)
    label = "rows" if agg == "count" else f"{agg} of {value}"
    total = series.sum() if agg in ("sum", "count") else None
    top = series.head(n)
    table_rows = [[str(k), fmt(x), pct(100 * x / total) if total else "-"] for k, x in top.items()]
    facts = []
    if not top.empty:
        first, x = next(iter(top.items()))
        share = f", {pct(100 * x / total)} of the total {fmt(total)}" if total else ""
        facts.append(Fact(f"By {label}, the top {group} is {first} ({fmt(x)}{share}).",
                          (fmt(x), *((pct(100 * x / total), fmt(total)) if total else ()))))
        if len(series) > 1:
            last, y = list(series.items())[-1]
            facts.append(Fact(f"The lowest {group} by {label} is {last} ({fmt(y)}).", (fmt(y),)))
    shown = series.head(max(n, CHART_BARS))   # a one-bar chart says nothing; the table keeps n
    chart = {"kind": "bar", "title": f"{label} by {group}", "x": [str(k) for k in shown.index],
             "series": {label: [float(x) for x in shown]}}
    return Result("top_n", cols, f"Top {group} by {label}", (group, label, "share"), rows(table_rows),
                  tuple(facts), chart)


def composition(table: Table, cols: dict, params: dict) -> Result:
    group, value = cols["group"], cols["value"]
    series = _grouped(table, group, value, "sum")
    total = series.sum()
    head = series.head(COMPOSITION_TOP)
    if len(series) > COMPOSITION_TOP:
        head = pd.concat([head, pd.Series({"Other": series.iloc[COMPOSITION_TOP:].sum()})])
    shares = [(str(k), x, 100 * x / total if total else 0.0) for k, x in head.items()]
    facts = []
    if shares:
        k, x, s = shares[0]
        facts.append(Fact(f"{k} is the largest share of {value}: {pct(s)} ({fmt(x)} of {fmt(total)}).",
                          (pct(s), fmt(x), fmt(total))))
        top3 = sum(x for k, x, _ in shares[:3] if k != "Other")
        if len(shares) > 3 and total:
            facts.append(Fact(f"The top 3 {group} values make up {pct(100 * top3 / total)} of {value}.",
                              (pct(100 * top3 / total),)))
    chart = {"kind": "bar", "title": f"Share of {value} by {group}", "x": [k for k, _, _ in shares],
             "series": {f"share of {value} (%)": [round(float(s), 1) for _, _, s in shares]}}
    return Result("composition", cols, f"Share of {value} by {group}", (group, value, "share"),
                  rows([[k, fmt(x), pct(s)] for k, x, s in shares]), tuple(facts), chart)


def compare_groups(table: Table, cols: dict, params: dict) -> Result:
    group, value = cols["group"], cols["value"]
    stats = table.frame.groupby(group)[value].agg(["mean", "median", "count"]).dropna(subset=["mean"])
    stats = stats.sort_values("mean", ascending=False)
    small = [str(k) for k, c in stats["count"].items() if c < SMALL_GROUP]
    table_rows = [[str(k), fmt(r["mean"]), fmt(r["median"]), fmt(r["count"])] for k, r in stats.iterrows()]
    facts = []
    if len(stats) >= 2:
        hi, lo = stats.index[0], stats.index[-1]
        m_hi, m_lo = stats.loc[hi, "mean"], stats.loc[lo, "mean"]
        facts.append(Fact(f"Mean {value} is highest for {hi} ({fmt(m_hi)}) and lowest for {lo} ({fmt(m_lo)}), "
                          f"a difference of {fmt(m_hi - m_lo)}.", (fmt(m_hi), fmt(m_lo), fmt(m_hi - m_lo))))
    flags = ((f"Some {group} groups have fewer than {SMALL_GROUP} rows ({', '.join(small[:5])}); their averages "
              "are unreliable."),) if small else ()
    if small:
        facts.append(Fact(f"{len(small)} {group} groups have fewer than {SMALL_GROUP} rows.", (str(len(small)),)))
    chart = {"kind": "bar", "title": f"Mean {value} by {group}", "x": [str(k) for k in stats.index[:20]],
             "series": {f"mean {value}": [float(x) for x in stats["mean"].head(20)]}}
    return Result("compare_groups", cols, f"{value} by {group}", (group, "mean", "median", "rows"),
                  rows(table_rows), tuple(facts), chart, flags)


def correlation(table: Table, cols: dict, params: dict) -> Result:
    a, b = cols["a"], cols["b"]
    pair = table.frame[[a, b]].dropna()
    n = len(pair)
    r = float(pair[a].corr(pair[b])) if n >= 3 else float("nan")
    reported = n >= CORR_MIN_N and abs(r) >= CORR_MIN_R
    if reported:
        sign = "positively" if r > 0 else "negatively"
        fact = Fact(f"{a} is {sign} associated with {b} (r = {r:.2f}, n = {fmt(n)}); this is an association, "
                    "not a cause.", (f"{r:.2f}", fmt(n)))
    else:
        fact = Fact(f"No association strong enough to report between {a} and {b} (needs at least {CORR_MIN_N} "
                    f"paired rows and |r| of at least {CORR_MIN_R}).", ())
    shown = f"{r:.2f}" if pd.notna(r) else "n/a"
    return Result("correlation", cols, f"{a} vs {b}", ("pair", "r", "n"), rows([[f"{a} / {b}", shown, fmt(n)]]),
                  (fact,), extra={"reported": reported})


def outliers(table: Table, cols: dict, params: dict) -> Result:
    name = cols["value"]
    s = table.frame[name].dropna()
    if len(s) < 4:
        return Result("outliers", cols, f"Outliers in {name}", facts=(Fact(f"Too few {name} values for outliers."),))
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    lo, hi = q1 - OUTLIER_K * (q3 - q1), q3 + OUTLIER_K * (q3 - q1)
    out = s[(s < lo) | (s > hi)]
    first = table.frame.columns[0]
    extreme = (out - s.median()).abs().sort_values(ascending=False).head(EXTREME_IDS).index
    ids = [str(table.frame.at[i, first]) for i in extreme]
    share = 100 * len(out) / len(s)
    facts = [Fact(f"{fmt(len(out))} of {fmt(len(s))} {name} values ({pct(share)}) fall outside the Tukey fences "
                  f"({fmt(lo)} to {fmt(hi)}).", (fmt(len(out)), fmt(len(s)), pct(share), fmt(lo), fmt(hi)))]
    if ids:
        facts.append(Fact(f"The most extreme {name} rows ({first}): {', '.join(ids)}.", tuple(ids)))
    table_rows = [[str(table.frame.at[i, first]), fmt(s[i])] for i in extreme]
    return Result("outliers", cols, f"Outliers in {name}", (first, name), rows(table_rows), tuple(facts))
