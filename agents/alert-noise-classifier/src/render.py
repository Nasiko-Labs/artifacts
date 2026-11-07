"""Pure: markdown for people, rendered in code from the stored result. No raw HTML, no images; text from the
paste is cleaned before it goes into a table cell. Empty sections read "None found"."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from fires import fmt_dur
from tune import label_secs

DEFAULT_ROWS = 20
TRADE_OFF = "_Tuning trades paging latency for fewer pages; \"noise\" never means \"safe to delete\"._"
_HTML = re.compile(r"<[^>]{0,500}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
SECTIONS = (("noise", "Noise"), ("actionable", "Actionable"), ("unclear", "Unclear"))


def clean(text: str) -> str:
    out = _IMAGE.sub("", _HTML.sub("", str(text or "")))
    return " ".join(out.replace("|", "\\|").split())


def _pct(value: float) -> str:
    return f"{value:g}%"


def headline(result: Mapping[str, Any]) -> str:
    t, p = result["totals"], result["period"]
    days = f"{p['days']} day{'s' if p['days'] != 1 else ''}"
    return (f"**{t['fires']} fires in {days}, {t['groups']} distinct alerts: {t['actionable']} actionable, "
            f"{t['noise']} noise ({_pct(t['noise_share_pct'])} of fires), {t['unclear']} unclear**\n"
            f"_Period {p['start']} to {p['end']} (times as pasted)._")


def table(groups: Sequence[Mapping[str, Any]], limit: int | None = DEFAULT_ROWS) -> str:
    if not groups:
        return "None found."
    shown = groups if limit is None else groups[:limit]
    rows = ["| Alert | Fires | Why |", "|---|---|---|"]
    rows += [f"| {clean(g['display'])} | {g['signals']['n']} | {clean(g['reason'])} |" for g in shown]
    if len(groups) > len(shown):
        rows.append(f"\n_{len(groups) - len(shown)} more not shown; ask \"only <section>\" for the full list._")
    return "\n".join(rows)


def section(result: Mapping[str, Any], label: str, title: str, limit: int | None = DEFAULT_ROWS) -> str:
    groups = [g for g in result["groups"] if g["label"] == label]
    extra = " (needs more history)" if label == "unclear" and groups else ""
    return f"**{title}** ({len(groups)}){extra}\n\n{table(groups, limit)}"


def suggestion_line(i: int, s: Mapping[str, Any]) -> str:
    text = f"{i}. {clean(s['name'])}: {clean(s['text'])}."
    if s["est_fires_saved"] is not None:
        text += (f" Would have avoided {s['est_fires_saved']} of {s['fires']} fires "
                 f"({s['est_pages_saved']} of {s['pages']} pages).")
    else:
        text += " Pages saved: not estimated."
    return text + f" Assumes: {clean(s['assumption'])}."


def suggestions(result: Mapping[str, Any], items: Sequence[Mapping[str, Any]] | None = None) -> str:
    chosen = result["tune"] if items is None else items
    if not chosen:
        return "**Tuning suggestions**\n\nNone found."
    t = result["totals"]
    lines = [suggestion_line(i, s) for i, s in enumerate(chosen, 1)]
    total = (f"\n\nApplied together: {t['pages_saved_if_applied']} of {t['pages_before']} pages avoided "
             f"({_pct(t['pages_saved_share_pct'])}).") if items is None else ""
    return "**Tuning suggestions**\n\n" + "\n".join(lines) + total


def footer(result: Mapping[str, Any], extra: Sequence[str] = ()) -> str:
    c = result["checks"]
    notes = [*result["coverage"], *(f"Not computed: {n}." for n in result["not_computed"])]
    if c["rows_skipped"]:
        notes.append(f"{c['rows_skipped']} of {c['rows_seen']} rows skipped (no readable alert name or time).")
    if c["notes_by_model"]:
        notes.append(f"{c['notes_by_model']} responder notes were labelled by the model; everything else is code.")
    if c["dropped"]:
        notes.append(f"{c['dropped']} model labels for unknown notes were dropped.")
    lines = [f"_{n}_" for n in (*notes, *extra)]
    return "\n".join(["_Estimates from the rows you pasted._", *lines, TRADE_OFF])


def full(result: Mapping[str, Any], extra: Sequence[str] = ()) -> str:
    body = [headline(result)] + [section(result, label, title) for label, title in SECTIONS]
    return "\n\n".join([*body, suggestions(result), footer(result, extra)])


def only(result: Mapping[str, Any], label: str) -> str:
    title = dict(SECTIONS)[label]
    parts = [headline(result), section(result, label, title, limit=None)]
    if label == "noise":
        parts.append(suggestions(result))
    return "\n\n".join(parts)


def detail(result: Mapping[str, Any], g: Mapping[str, Any]) -> str:
    s = g["signals"]
    own = [x for x in result["tune"] if x["group_key"] == g["key"]]
    lines = [f"**{clean(g['display'])}: {g['label']}**", f"Why: {clean(g['reason'])}.", "", numbers_table([g]), ""]
    lines += [suggestions(result, own)] if own else []
    lines += ["", "Sources:"] + [_evidence_line(e) for e in g["evidence"]]
    lines += [f"_Off-hours share {_pct(round(100 * (s['off_hours_share'] or 0), 1))} (shown, not used to classify)._"]
    return "\n".join(lines)


def _evidence_line(e: Mapping[str, Any]) -> str:
    return f"- row {e['row']}: `{e['text'].replace('`', chr(39))}`" if e["text"] else f"- row {e['row']}"


def numbers_table(groups: Sequence[Mapping[str, Any]]) -> str:
    rows = [("| Alert | Label | Fires | Pages | Median | p90 | <5 min | Auto-resolved | Ack, no action | Actions | "
             "Flaps |"), "|---|---|---|---|---|---|---|---|---|---|---|"]
    for g in groups:
        s = g["signals"]
        rows.append(f"| {clean(g['display'])} | {g['label']} | {s['n']} | {s['pages']} | {fmt_dur(s['median_dur'])} | "
                    f"{fmt_dur(s['p90_dur'])} | {s['short']}/{s['with_duration']} | {s['auto_resolved']} | "
                    f"{s['ack_no_action']}/{s['touched']} | {s['actions']} | {s['flap']} |")
    return "\n".join(rows)


def sources(result: Mapping[str, Any]) -> str:
    out = [headline(result)]
    for g in result["groups"]:
        out.append(f"**{clean(g['display'])}** ({g['label']})\n" + "\n".join(_evidence_line(e) for e in g["evidence"]))
    return "\n\n".join(out)


def top(result: Mapping[str, Any], n: int) -> str:
    ranked = sorted((g for g in result["groups"] if g["label"] == "noise"),
                    key=lambda g: (-_saved(result, g), -g["signals"]["pages"], -g["signals"]["n"]))[:n]
    if not ranked:
        return f"**Top {n} to fix**\n\nNone found: no alert was classified as noise."
    lines = [f"**Top {len(ranked)} to fix** (ranked by pages the suggestions would avoid)"]
    for i, g in enumerate(ranked, 1):
        own = [x for x in result["tune"] if x["group_key"] == g["key"]]
        fix = "; ".join(clean(x["text"]) for x in own) or "no suggestion"
        lines.append(f"{i}. {clean(g['display'])}: {g['signals']['n']} fires, {_saved(result, g)} pages avoidable. "
                     f"{fix}. Why: {clean(g['reason'])}.")
    return "\n".join(lines)


def _saved(result: Mapping[str, Any], g: Mapping[str, Any]) -> int:
    own = [x["est_pages_saved"] or 0 for x in result["tune"] if x["group_key"] == g["key"]]
    return min(max(own, default=0), g["signals"]["pages"])


def what_if(rows: Sequence[Mapping[str, Any]], secs: int) -> str:
    if not rows:
        return f"**What if `for: {label_secs(secs)}`**\n\nNone found: no alert was classified as noise."
    out = [f"**What if every noise alert had `for: {label_secs(secs)}`** (recomputed from the stored durations)",
           "| Alert | Fires | Pages | Fires avoided | Pages avoided |", "|---|---|---|---|---|"]
    out += [f"| {clean(r['name'])} | {r['fires']} | {r['pages']} | {r['fires_saved']} | {r['pages_saved']} |"
            for r in rows]
    pages = sum(r["pages"] for r in rows)
    saved = sum(r["pages_saved"] for r in rows)
    missing = sum(r["no_duration"] for r in rows)
    out.append(f"\nTotal: {saved} of {pages} noise pages avoided. Assumes: fires shorter than {label_secs(secs)} "
               f"would stop paging; a real outage would page up to {label_secs(secs)} later.")
    if missing:
        out.append(f"_{missing} fires had no duration and are not counted._")
    return "\n".join(out)


def as_json(envelope: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
