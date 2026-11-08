"""Rendering in code: markdown only, no raw HTML, no images, URLs only from the index or tool output.
Chat markup (`<@U1>`, `<#C1|name>`, `<https://x|label>`) is flattened and any remaining angle bracket
replaced, so nothing from a message or a sheet can become HTML."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

TEXT_CHARS = 400
_LINK_LABEL = re.compile(r"<(https?://[^|>\s]+)\|([^>]*)>")
_BARE_LINK = re.compile(r"<(https?://[^>\s]+)>")
_CHANNEL_REF = re.compile(r"<#[A-Z0-9]+\|([^>]*)>")
_USER_REF = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_SPECIAL = re.compile(r"<!(channel|here|everyone)[^>]*>")
_IMAGE = re.compile(r"!\[")
_LABELS = {"exact": "Exact match", "partial": "Partial match", "none": "No match"}


def clean(text: str, limit: int = TEXT_CHARS) -> str:
    out = _LINK_LABEL.sub(r"\2 (\1)", text or "")
    out = _BARE_LINK.sub(r"\1", out)
    out = _CHANNEL_REF.sub(r"#\1", out)
    out = _USER_REF.sub(r"@\1", out)
    out = _SPECIAL.sub(r"@\1", out)
    out = _IMAGE.sub("[", out)
    out = " ".join(out.replace("<", "‹").replace(">", "›").replace("`", "'").split())
    return out if len(out) <= limit else out[:limit] + "…"


def quote(text: str) -> str:
    return "> " + clean(text, 1200)


def copyable(number: int, reason: str, body: str) -> str:
    return f"#{number}: {reason} Here is the reply to copy:\n\n{quote(body)}"


def headline(match: Mapping) -> str:
    label = _LABELS[match["confidence"]]
    if match["dashboard"] is None:
        return f"**{label}**"
    return f"**{label}: \"{clean(match['dashboard'], 120)}\"** ([open]({match['url']}))"


def item_block(number: int | None, item: Mapping, show_request: bool) -> str:
    match = item["match"]
    lines = []
    if show_request:
        who = clean(item["requester"] or "someone", 60)
        link = f" ([message]({item['request_link']}))" if item.get("request_link") else ""
        lines.append(f"{number}. {who} asked: \"{clean(item['request_text'], 300)}\"{link}")
    lines.append(headline(match))
    if match["covers"] or match["missing"]:
        covers = ", ".join(clean(c, 80) for c in match["covers"]) or "-"
        missing = ", ".join(clean(m, 80) for m in match["missing"]) or "-"
        lines.append(f"Covers: {covers}. Missing: {missing}.")
    lines.append(f"Why: {clean(match['why'], 300)}")
    lines.append("Draft reply:\n" + quote(item["reply"]))
    return "\n".join(lines)


def single(env: Mapping, draft_only: bool) -> str:
    item = env["requests"][0]
    parts = [item_block(None, item, show_request=False), _index_line(env)]
    parts += [f"_Note: {clean(n, 400)}_" for n in env["coverage"]]
    parts.append(f"_{'Draft only: nothing will be posted.' if draft_only else 'Copy the reply into the thread.'}_")
    return "\n\n".join(p for p in parts if p)


def scan(env: Mapping, label: str, days: int, skipped: int, pending: int, posting: bool) -> str:
    items = env["requests"]
    plural = "s" if len(items) != 1 else ""
    head = f"**Checked {clean(label, 80)} (last {days} day{'s' if days != 1 else ''}): {len(items)} request{plural}"
    head += f", {skipped} other message{'s' if skipped != 1 else ''} skipped**" if skipped else "**"
    parts = [head]
    parts += [item_block(n, item, show_request=True) for n, item in enumerate(items, start=1)]
    if not items:
        parts.append("No data requests there in that window.")
    parts.append(_index_line(env))
    parts += [f"_Note: {clean(n, 400)}_" for n in env["coverage"]]
    if pending:
        parts.append(f"{pending} more request{'s' if pending != 1 else ''} waiting: say \"continue\".")
    if items and posting:
        parts.append("Each reply is posted only after you approve it, one card at a time.")
    return "\n\n".join(p for p in parts if p)


def _index_line(env: Mapping) -> str:
    info = env.get("index")
    if not info:
        return ""
    return f"Matched against \"{clean(info['title'], 80)}\" ({info['rows']} dashboard{'s' if info['rows'] != 1 else ''})."


def as_json(env: Mapping) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"


def numbered(items: Sequence[Mapping]) -> str:
    return "\n".join(f"{n}. {clean(i['request_text'], 120)}" for n, i in enumerate(items, start=1))
