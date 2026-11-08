"""Pure: the markdown reply, rendered in code from the (viewed) result. Alphabetical people, newest example
first, evidence per line, counts labelled as evidence items. No raw HTML, no images, links only from records."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import dates
import envelope
from view import UNATTRIBUTED, VAGUE, View

HEADER = "**Recognition examples** ({records}, {people}; alphabetical; counts are evidence items, not a ranking)"
DEFAULT_VIEW = View()
NOBODY = "Nobody matches that filter in the collected examples."


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def counts_label(counts: Mapping[str, int]) -> str:
    bits = [_plural(counts["prs"], "PR") if counts["prs"] else "",
            f"{counts['praise']} praise" if counts["praise"] else "",
            f"{counts['shipped']} shipped" if counts["shipped"] else "",
            f"{counts['unspecific_praise']} too-general praise" if counts["unspecific_praise"] else ""]
    return ", ".join(b for b in bits if b) or "no evidence items"


def _person(p: Mapping[str, Any]) -> list[str]:
    lines = [f"**{p['name']}** ({counts_label(p['counts'])})"]
    lines += [f"- {dates.short(e['date'])} - {e['text']} ({e['evidence']})" for e in p["examples"]]
    if p.get("more"):
        lines.append(f"- and {p['more']} more (ask \"show all\")")
    return lines


def _unattributed(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return []
    return [f"**Unattributed ({len(items)})**"] + [f"- {i['record_id']}: {i['text']} ({i['reason']})" for i in items]


def _vague(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["No praise was too general to use."]
    lines = [f"**Too general to use as examples ({len(items)})**"]
    for v in items:
        who = f" ({', '.join(v['people'])})" if v["people"] else ""
        lines.append(f"- {v['speaker'] or 'Unknown'}: \"{v['quote']}\"{who}")
    return lines


def render(result: Mapping[str, Any], view: View = DEFAULT_VIEW, notes: Sequence[str] = ()) -> str:
    if view.section == UNATTRIBUTED:
        body = _unattributed(result["unattributed"]) or ["Every record was attributed to someone."]
        return "\n".join(body)
    if view.section == VAGUE:
        return "\n".join(_vague(result["vague"]))
    s = envelope.stats(result)
    blocks = [HEADER.format(records=_plural(s["records"], "record"), people=_plural(s["people"], "person", "people"))]
    blocks += ["\n".join(_person(p)) for p in result["per_person"]] or [NOBODY]
    if view.show_quotes:
        blocks.append("\n".join(_vague(result["vague"])))
    elif not view.names and not view.kinds:
        unattributed = "\n".join(_unattributed(result["unattributed"]))
        blocks += [unattributed] if unattributed else []
    caveats = [*result["caveats"], *notes]
    if result.get("hidden_below_min"):
        caveats.append(f"{_plural(result['hidden_below_min'], 'person', 'people')} with fewer examples than the "
                       "minimum not shown.")
    if caveats:
        blocks.append("_" + " ".join(caveats) + "_")
    return "\n\n".join(blocks)


def as_json(data: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"
