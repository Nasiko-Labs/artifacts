"""Pure: tuning suggestions for noise groups, from code templates. At most two per group, first match in the
plan's order (for: → route to a ticket → inhibit → retire → threshold → periodic). Every suggestion carries
its assumption and a computed pages-saved estimate (None when it cannot be estimated). Nothing is invented: a
threshold is suggested only when the rows carry both a value and a threshold."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from signals import percentile

FOR_CHOICES_SECS = (300, 600, 900, 1800)
ROUTE_MIN_FIRES = 10
RETIRE_MIN_FIRES = 10
MAX_PER_GROUP = 2


def label_secs(secs: int) -> str:
    return f"{secs // 60}m"


def saved_with_for(timeline: Sequence[Sequence[Any]], secs: float) -> tuple[int, int]:
    """(fires, pages) that resolved in under `secs` — they would not have paged with `for: secs`."""
    short = [row for row in timeline if row[0] is not None and row[0] < secs]
    return len(short), sum(1 for row in short if row[1])


def _for(g: Mapping[str, Any]) -> dict | None:
    durs = [row[0] for row in g["timeline"] if row[0] is not None and row[0] < FOR_CHOICES_SECS[-1]]
    if not durs:
        return None
    p90 = percentile(durs, 0.9)
    secs = next(c for c in FOR_CHOICES_SECS if c >= p90)
    fires, pages = saved_with_for(g["timeline"], secs)
    if not fires:
        return None
    value = label_secs(secs)
    return _make(g, "for", f"add or lengthen `for: {value}`", fires, pages,
                 f"fires shorter than {value} would stop paging; a real outage would page up to {value} later",
                 for_secs=secs)


def _route(g: Mapping[str, Any]) -> dict | None:
    s = g["signals"]
    if s["pages"] < 1 or s["actions"] or s["n"] < ROUTE_MIN_FIRES:
        return None
    return _make(g, "route", "route it to a ticket or chat channel instead of a page", s["n"], s["pages"],
                 "nobody needs to be woken for it; someone still reviews the ticket queue")


def _inhibit(g: Mapping[str, Any]) -> dict | None:
    s = g["signals"]
    if not s["storm_parent"]:
        return None
    return _make(g, "inhibit", f"inhibit {g['name']} while {s['storm_parent']} is firing (or group them)",
                 s["storm_hits"], s["storm_hit_pages"],
                 f"{s['storm_parent']} already pages for the same failure; a lone {g['name']} still pages")


def _retire(g: Mapping[str, Any]) -> dict | None:
    s = g["signals"]
    if s["n"] < RETIRE_MIN_FIRES or s["actions"]:
        return None
    return _make(g, "retire", f"no one acted in {s['n']} fires over {s['span_days']} days; retire it or move it "
                 "to a dashboard", s["n"], s["pages"], "the pasted period is representative of how it behaves")


def _threshold(g: Mapping[str, Any]) -> dict | None:
    values, thresholds = g.get("values") or [], g.get("thresholds") or []
    if not values or not thresholds:
        return None
    low, high = min(values), max(values)
    t = thresholds[0]
    return _make(g, "threshold", f"fired values ranged {_num(low)}-{_num(high)} against threshold {_num(t)}; "
                 f"consider a threshold above {_num(high)}", None, None,
                 "the values seen in this period are the ones that should not page")


def _periodic(g: Mapping[str, Any]) -> dict | None:
    if not g["signals"]["periodic"]:
        return None
    return _make(g, "periodic", "alert on the job's failure, not on its schedule", None, None,
                 "the alert tracks a scheduled job that runs as designed")


def _num(value: float) -> str:
    return f"{value:g}"


def _make(g, change_type: str, text: str, fires: int | None, pages: int | None, assumption: str,
          for_secs: int | None = None) -> dict:
    return {"group_key": g["key"], "name": g["name"], "change_type": change_type, "text": text,
            "assumption": assumption, "est_fires_saved": fires, "est_pages_saved": pages,
            "pages": g["signals"]["pages"], "fires": g["signals"]["n"], "for_secs": for_secs}


RULES = (_for, _route, _inhibit, _retire, _threshold, _periodic)


def suggest(g: Mapping[str, Any]) -> list[dict]:
    out = []
    for rule in RULES:
        made = rule(g)
        if made is not None:
            out.append(made)
        if len(out) == MAX_PER_GROUP:
            break
    return out


def totals(groups: Sequence[Mapping[str, Any]], suggestions: Sequence[Mapping[str, Any]]) -> dict:
    pages_before = sum(g["signals"]["pages"] for g in groups)
    saved = 0
    for g in groups:
        own = [s["est_pages_saved"] for s in suggestions if s["group_key"] == g["key"] and s["est_pages_saved"]]
        saved += min(max(own), g["signals"]["pages"]) if own else 0
    share = round(100 * saved / pages_before, 1) if pages_before else 0.0
    return {"pages_before": pages_before, "pages_saved_if_applied": saved, "pages_saved_share_pct": share}


def what_if_for(groups: Sequence[Mapping[str, Any]], secs: int) -> list[dict]:
    """Recompute the pages a `for: secs` would have saved on every noise group, from the stored durations."""
    rows = []
    for g in groups:
        if g["label"] != "noise":
            continue
        fires, pages = saved_with_for(g["timeline"], secs)
        rows.append({"name": g["display"], "fires": g["signals"]["n"], "pages": g["signals"]["pages"],
                     "fires_saved": fires, "pages_saved": pages,
                     "no_duration": sum(1 for row in g["timeline"] if row[0] is None)})
    return rows
