"""Pure: the counting analyses of the closed menu: missingness, duplicates and keys, distribution of a
category, and a cross tab of two categories (counts, or sums of a numeric column)."""

from __future__ import annotations

from fmt import fmt, pct
from profiling import ID_NAME, Table
from result import Fact, Result, rows

MISSING_MIN_PCT = 5.0
DISTRIBUTION_TOP = 10
CROSS_TOP = 5


def missingness(table: Table, cols: dict, params: dict) -> Result:
    gaps = sorted(((c.name, c.nulls_pct) for c in table.profile.columns if c.nulls_pct >= MISSING_MIN_PCT),
                  key=lambda g: -g[1])
    if not gaps:
        fact = Fact(f"No column has {pct(MISSING_MIN_PCT)} or more blank values.", ())
    else:
        listed = ", ".join(f"{name} ({pct(share)})" for name, share in gaps[:6])
        fact = Fact(f"{len(gaps)} columns have {pct(MISSING_MIN_PCT)} or more blank values: {listed}.",
                    (str(len(gaps)), *(pct(share) for _, share in gaps[:6])))
    return Result("missingness", cols, "Blank values", ("column", "blank"),
                  rows([[name, pct(share)] for name, share in gaps]), (fact,))


def duplicates_and_keys(table: Table, cols: dict, params: dict) -> Result:
    profile = table.profile
    facts = [Fact(f"{fmt(profile.duplicate_rows)} rows are exact duplicates of another row.",
                  (fmt(profile.duplicate_rows),))] if profile.duplicate_rows else [
        Fact("No row is an exact duplicate of another.", ())]
    table_rows = [["duplicate rows", fmt(profile.duplicate_rows)]]
    for column in profile.columns:
        if column.dtype != "id" and not ID_NAME.search(column.name):
            continue
        values = table.frame[column.name].dropna()
        repeated = int(len(values) - values.nunique())
        table_rows.append([f"repeated {column.name}", fmt(repeated)])
        if repeated:
            facts.append(Fact(f"{fmt(repeated)} {column.name} values repeat, so {column.name} is not a unique key.",
                              (fmt(repeated),)))
    return Result("duplicates_and_keys", cols, "Duplicates and keys", ("check", "count"), rows(table_rows),
                  tuple(facts))


def distribution(table: Table, cols: dict, params: dict) -> Result:
    group = cols["group"]
    values = table.frame[group].dropna()
    counts = values.value_counts().head(DISTRIBUTION_TOP)
    total = len(values)
    table_rows = [[str(k), fmt(c), pct(100 * c / total)] for k, c in counts.items()]
    facts = []
    if total:
        k, c = next(iter(counts.items()))
        facts.append(Fact(f"The most common {group} is {k}: {fmt(c)} of {fmt(total)} rows ({pct(100 * c / total)}).",
                          (fmt(c), fmt(total), pct(100 * c / total))))
        facts.append(Fact(f"{group} has {fmt(values.nunique())} distinct values.", (fmt(values.nunique()),)))
    chart = {"kind": "bar", "title": f"Rows by {group}", "x": [str(k) for k in counts.index],
             "series": {"rows": [float(c) for c in counts]}}
    return Result("distribution", cols, f"Rows by {group}", (group, "rows", "share"), rows(table_rows),
                  tuple(facts), chart)


def cross_tab(table: Table, cols: dict, params: dict) -> Result:
    a, b, value = cols["a"], cols["b"], cols.get("value")
    frame = table.frame.dropna(subset=[a, b])
    top_a = frame[a].value_counts().head(CROSS_TOP).index
    top_b = frame[b].value_counts().head(CROSS_TOP).index
    frame = frame[frame[a].isin(top_a) & frame[b].isin(top_b)]
    if value:
        grid = frame.pivot_table(index=a, columns=b, values=value, aggfunc="sum", fill_value=0)
        what = f"sum of {value}"
    else:
        grid = frame.groupby([a, b]).size().unstack(fill_value=0)
        what = "rows"
    grid = grid.reindex(index=[k for k in top_a if k in grid.index], columns=[k for k in top_b if k in grid.columns])
    table_rows = [[str(idx), *(fmt(x) for x in row)] for idx, row in grid.iterrows()]
    facts = []
    if grid.size:
        stacked = grid.stack()
        (ka, kb), top = stacked.idxmax(), stacked.max()
        facts.append(Fact(f"The largest {a} × {b} cell by {what} is {ka} / {kb} ({fmt(top)}).", (fmt(top),)))
    return Result("cross_tab", cols, f"{a} by {b} ({what})", (a, *(str(c) for c in grid.columns)),
                  rows(table_rows), tuple(facts))
