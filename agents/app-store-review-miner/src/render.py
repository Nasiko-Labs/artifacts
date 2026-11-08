"""Pure: the markdown reply, rendered in code from the envelope (no raw HTML, no images; quotes are
neutralised for markdown). The JSON view is the envelope itself in a fenced block."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import replies

_STORE = {"ios": "iOS", "android": "Android"}


def safe(text: Any) -> str:
    """Review text inside markdown: one line, no tags, no image syntax, no table breaks."""
    out = " ".join(str(text).split()).replace("<", "‹").replace(">", "›").replace("![", "! [")
    return out.replace("|", "/").replace("`", "'")


def safe_block(text: str) -> str:
    return "\n".join(safe(line) for line in str(text).splitlines())


def _num(value: Any, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:g}{suffix}"


def overview_line(o: Mapping[str, Any]) -> str:
    stores = ", ".join(f"{_STORE.get(k, k)} {v}" for k, v in o["stores"].items())
    span = f", {o['date_from']}..{o['date_to']}" if o.get("date_from") else ""
    return (f"**{safe(o['app'])}**, {o['reviews']} reviews{f' ({stores})' if stores else ''}{span}, "
            f"avg {_num(o['avg_rating'])} ({o['rated']} rated).")


def versions_table(rows: list[Mapping[str, Any]]) -> str:
    lines = ["**By version**", "", "| Version | Reviews | Avg | Low (1-2 stars) | Top complaints | Flag |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        flag = safe(r.get("flag") or "")
        flag = f"**{flag[:20].upper()}**{flag[20:]}" if flag.startswith("regression suspected") else flag
        lines.append(f"| {safe(r['version'])} | {r['reviews']} | {_num(r['avg_rating'])} | {_num(r['low_share'], '%')} | "
                     f"{safe('; '.join(r['top_complaints'])) or '-'} | {flag or '-'} |")
    return "\n".join(lines)


def themes_block(env: Mapping[str, Any]) -> str:
    ours = [t for t in env["themes"] if t["app"] == env["ours"]]
    lines = [f"**Complaint themes ({safe(env['ours'])})**", ""]
    if not ours:
        lines.append("No complaint theme reached 3 reviews.")
    for n, t in enumerate(ours, start=1):
        new = f", new in {t['new_in']}" if t.get("new_in") else ""
        lines.append(f"{n}. **{safe(t['name'])}**: {t['count']} reviews ({t['share']:g}% of reviews with a complaint), "
                     f"avg {_num(t['avg_rating'])}{new}")
        lines.extend(f'   - "{safe(q)}"' for q in t["quotes"])
    below = [b for b in env.get("below_the_bar", ())]
    if below:
        lines.append("\nBelow the bar (fewer than 3 reviews): "
                     + "; ".join(f"{safe(b['name'])} ({b['count']})" for b in below[:10])
                     + (f"; and {len(below) - 10} more" if len(below) > 10 else ""))
    return "\n".join(lines)


def compare_block(env: Mapping[str, Any]) -> str:
    parts = []
    for comp in env.get("competitors", ()):
        rows = [r for r in env["competitor_comparison"] if r["app"] == comp["app"]]
        small = "; small sample" if comp.get("small_sample") else ""
        lines = [f"**Versus {safe(comp['app'])}** ({comp['reviews']} reviews{small}, avg {_num(comp['avg_rating'])})", "",
                 "| Theme | Ours | Theirs | Delta (points) | Note |", "|---|---|---|---|---|"]
        lines.extend(f"| {safe(r['theme'])} | {r['our_share']:g}% ({r['our_count']}) | {r['their_share']:g}% "
                     f"({r['their_count']}) | {r['delta']:+g} | {_row_note(r['note'])} |" for r in rows[:12])
        if not rows:
            lines = lines[:1] + ["No theme reached 3 reviews on either side."]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _row_note(note: str) -> str:
    return "they have it, we do not hear about it" if note.startswith("they have it") else "-"


def markdown(env: Mapping[str, Any]) -> str:
    sections = [overview_line(env["overview"])]
    if env["checks"].get("salvage"):
        sections.append(replies.STEP_LIMIT_NOTE)
    sections.append(versions_table(env["by_version"]))
    sections.append(themes_block(env))
    if env.get("praise"):
        sections.append("**Praise heard most:** " + "; ".join(f"{safe(p['label'])} ({p['count']})" for p in env["praise"]))
    if env.get("competitors"):
        sections.append(compare_block(env))
    notes = "\n".join(f"- {safe(c)}" for c in env["caveats"])
    sections.append(f"**Notes**\n{notes}\n\nConfidence: {env['confidence']}{_attention(env)}")
    return "\n\n".join(s for s in sections if s)


def _attention(env: Mapping[str, Any]) -> str:
    if any((r.get("flag") or "").startswith("regression") for r in env["by_version"]):
        return "; a person should look at the flagged release."
    return "; a person should check these results." if env["needs_human"] else "."


def as_json(env: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(env, indent=1, ensure_ascii=False) + "\n```"
