"""Pure: markdown from the envelope and the stored result. No raw HTML, no images; every number from code."""

from __future__ import annotations

import json
import re

from report import Stored, envelope

SHOW_MAX_REPLIES = 10
SHOW_REPLY_CHARS = 300
_HTML = re.compile(r"<[^>\n]{1,200}>")
_IMAGE = re.compile(r"!\[")


def clean(text: str) -> str:
    return _IMAGE.sub("[", _HTML.sub("", text or ""))


def _cell(text: str) -> str:
    return " ".join(clean(text).replace("|", "/").split())


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line.strip() else ">" for line in clean(text).strip().splitlines())


def as_json(data: dict) -> str:
    return "```json\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n```"


def _table(items: list[dict], stored: Stored) -> str:
    head = ["#", "Pattern", "Replies", "Share"] + (["Agents"] if stored.has_agents else []) \
        + (["Tickets"] if stored.has_tickets else [])
    rows = []
    for item in items:
        cells = [str(item["id"]), _cell(item["pattern"]), str(item["frequency"]), f"{item['share']}%"]
        cells += [str(item["distinct_agents"])] if stored.has_agents else []
        cells += [str(item["distinct_tickets"])] if stored.has_tickets else []
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head), *rows])


def candidate_block(item: dict) -> str:
    tickets = f", e.g. tickets {', '.join(item['sample_ticket_ids'])}" if item["sample_ticket_ids"] else ""
    lines = [f"**{item['id']}. {_cell(item['pattern'])}** ({item['frequency']} replies{tickets})", _quote(item["draft"])]
    if item.get("note"):
        lines.append(f"_({_cell(item['note'])})_")
    return "\n".join(lines)


def _footer(data: dict, stored: Stored) -> str:
    bits = [f'a sentence shared by {b["share"]:g}% of replies: "{_cell(b["text"])[:120]}"'
            for b in data["boilerplate_ignored"][:3]]
    ignored = f"Ignored {'; '.join(bits)}." if bits else ""
    caveats = " ".join(data["caveats"])
    text = " ".join(p for p in (ignored, caveats) if p)
    return f"_({text})_" if text else ""


def render(stored: Stored, json_view: bool = False) -> tuple[str, dict]:
    data = envelope(stored)
    if json_view:
        return as_json(data), data
    items, covered = data["items"], data["covered_by_existing"]
    covered_note = f"; {len(covered)} already covered by existing macros" if covered else ""
    parts = [(f"**Recurring answers in {stored.total:,} replies** ({len(items)} candidates cover "
              f"{data['coverage_percent']}%{covered_note})")]
    if items:
        parts += [_table(items, stored), *(candidate_block(i) for i in items)]
    else:
        parts.append(f"No answer recurs at least {stored.min_freq} times in these replies. Try \"min 2\", or "
                     "paste more replies.")
    if covered:
        parts.append("**Already covered by a macro**\n" + "\n".join(
            f'- {_cell(c["pattern"])} ({c["frequency"]} replies): "{_cell(c["macro"])}"' for c in covered))
    parts.append("Drafts are proposals: nothing was created in any tool. Every count above was computed from your "
                 "replies, and each draft only states what at least two of its replies say.")
    footer = _footer(data, stored)
    return "\n\n".join(p for p in (*parts, footer) if p), data


def show_replies(stored: Stored, n: int) -> str | None:
    items = envelope(stored)["items"]
    if not 1 <= n <= len(items):
        return None
    group = stored.selection().candidates[n - 1][0]
    shown = [m for m in group.members if m in stored.texts][:SHOW_MAX_REPLIES]
    lines = [(f"**Replies behind {n}: {_cell(items[n - 1]['pattern'])}** ({group.frequency} replies; showing "
              f"{len(shown)}, customer data replaced by placeholders)")]
    for k, rid in enumerate(shown, start=1):
        ticket = f", ticket {stored.tickets[rid]}" if stored.tickets.get(rid) else ""
        text = " ".join(stored.texts[rid].split())
        cut = text[:SHOW_REPLY_CHARS] + (" ..." if len(text) > SHOW_REPLY_CHARS else "")
        lines.append(f"{k}. {rid}{ticket}: {_cell(cut)}")
    if group.frequency > len(shown):
        lines.append(f"_(and {group.frequency - len(shown)} more)_")
    return "\n".join(lines)


def boilerplate(stored: Stored) -> str:
    if not stored.boilerplate:
        return "I ignored no boilerplate: no sentence was shared by 40% or more of the replies."
    return "**Boilerplate I ignored** (sentences shared by 40% or more of the replies)\n" + "\n".join(
        f'- "{_cell(s)}" ({round(v * 100, 1):g}% of replies)' for s, v in stored.boilerplate)
