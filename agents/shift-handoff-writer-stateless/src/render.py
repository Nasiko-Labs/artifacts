"""Pure: the handoff as chat markdown (no HTML, no links), or the envelope as a fenced JSON block.

Views: default, hot (hot items only), short (hot + need to know), sources (every item with its quote and
entries), all (nothing capped)."""

from __future__ import annotations

import json

import handoff as hf
from items import OPEN, RESOLVED, WAITING, Item, label, owner_text
from know import CAP, NO_TIME, Know

RESOLVED_CAP = 10
VIEWS = ("default", "hot", "short", "sources", "all")
CUT_VIEWS = ("default", "hot", "short")   # views a rendered handoff already holds (sources/all need a rebuild)
_KEEP = {"default": None, "hot": ("**Shift handoff**", "**Headline:**", "**Hot ("),
         "short": ("**Shift handoff**", "**Headline:**", "**Hot (", "**Need to know**")}


def _item_line(item: Item, sources: bool) -> str:
    parts = [label(item), f"owner: {owner_text(item)}"]
    if item.customer:
        parts.append(f"customer: {item.customer}")
    if item.status == WAITING:
        parts.append(f"waiting on: {item.waiting_on}" if item.waiting_on else "waiting")
    if item.next_step:
        parts.append(f"next: {item.next_step}")
    if item.due:
        parts.append(f"due {item.due}" + (" (time not understood; not used for hot)" if item.due_unparsed else ""))
    line = " - ".join(parts)
    if item.hot_reasons:
        line += "  (why: " + "; ".join(f'{r.cue}: "{r.quote}"' for r in item.hot_reasons) + ")"
    if sources:
        line += f'\n   - source ({", ".join(item.entries)}): "{item.snippet}"'
        line += "".join(f'\n   - earlier: "{h}"' for h in item.history)
    return line


def _numbered(title: str, items: list[Item], sources: bool) -> list[str]:
    if not items:
        return []
    return [f"**{title} ({len(items)})**", *(f"{n}. {_item_line(i, sources)}" for n, i in enumerate(items, 1))]


def _know_line(entry: Know, sources: bool) -> str:
    if entry.kind == "promise":
        line = f"Promise: {entry.text} - time: {entry.time or NO_TIME}"
    elif entry.kind == "waiting":
        line = f"{entry.text} - follow up at {entry.time}"
    else:
        line = entry.text
    return line + (f'  (source: "{entry.quote}")' if sources and entry.kind != "waiting" else "")


def _know(entries: list[Know], capped: bool, sources: bool) -> list[str]:
    if not entries:
        return []
    shown = entries[:CAP] if capped else entries
    out = ["**Need to know**", *(f"- {_know_line(k, sources)}" for k in shown)]
    if len(entries) > len(shown):
        out.append(f"- {len(entries) - len(shown)} more (say \"show all\")")
    return out


def _resolved(items: list[Item], capped: bool) -> list[str]:
    if not items:
        return []
    shown = items[:RESOLVED_CAP] if capped else items
    out = [f"**Resolved today ({len(items)})**", *(f"- {label(i)}" for i in shown)]
    if len(items) > len(shown):
        out.append(f"- and {len(items) - len(shown)} more")
    return out


def _header(handoff: hf.Handoff, live: int) -> list[str]:
    extra = f"; next shift {handoff.next_shift}" if handoff.next_shift else ""
    out = f"**Shift handoff** (as of {handoff.at.strftime('%H:%M')}; {live} open items{extra})"
    return [out + (f"\n\n**Headline:** {handoff.headline}" if handoff.headline else "")]


def _footer(handoff: hf.Handoff, extra_notes: tuple[str, ...]) -> list[str]:
    notes = list(handoff.notes) + list(extra_notes)
    if handoff.dropped:
        notes.append(f"{handoff.dropped} extracted item(s) were left out: their quote wasn't found in your notes.")
    if any(i.due_unparsed for i in handoff.items):
        notes.append("Some due times weren't understood; they are shown as written and not used for hot.")
    return [f"_{n}_" for n in notes]


def render(handoff: hf.Handoff, view: str = "default", extra_notes: tuple[str, ...] = ()) -> str:
    items = hf.ordered(handoff.items, handoff.by_id)
    hot_items = [i for i in items if i.hot]
    open_items = [i for i in items if i.status == OPEN and not i.hot]
    waiting = [i for i in items if i.status == WAITING and not i.hot]
    sources = view == "sources"
    blocks = [_header(handoff, len(hot_items) + len(open_items) + len(waiting))]
    blocks.append(_numbered("Hot", hot_items, sources) or ["**Hot (0)**", "Nothing hot in these notes."])
    if view != "hot":
        know_entries = hf.need_to_know(handoff)
        if view != "short":
            blocks += [_numbered("Open", open_items, sources), _numbered("Waiting", waiting, sources)]
        blocks.append(_know(know_entries, view != "all", sources))
        if view != "short":
            blocks.append(_resolved([i for i in items if i.status == RESOLVED], view != "all"))
    blocks.append(_footer(handoff, extra_notes))
    return "\n\n".join("\n".join(b) for b in blocks if b)


def as_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def cut(markdown: str, view: str) -> str:
    """A view filtered from a handoff already rendered (from the chat history): its blocks, nothing rebuilt."""
    keep = _KEEP.get(view)
    if keep is None:
        return markdown
    blocks = [b for b in markdown.split("\n\n") if b.lstrip().startswith(keep)]
    return "\n\n".join(blocks) if blocks else markdown
