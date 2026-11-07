"""Pure: the envelope as markdown, rendered in code. No raw HTML, no images, and the only URLs are the
permalinks the tools returned. Chat markup like `<@U1>` or `<https://x|label>` is flattened to text."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

_LINK_LABEL = re.compile(r"<(?:https?://[^>|]+)\|([^>]+)>")
_ANGLE = re.compile(r"<([@#!]?)([^<>|]+)(?:\|[^<>]*)?>")


def clean(text: str) -> str:
    """Chat markup to plain text; any remaining angle bracket is neutralised (never raw HTML)."""
    text = _ANGLE.sub(r"\1\2", _LINK_LABEL.sub(r"\1", text or ""))
    return " ".join(text.replace("<", "‹").replace(">", "›").split())


def when(ts: str | float | None) -> str:
    try:
        moment = datetime.fromtimestamp(float(ts), UTC)
    except (TypeError, ValueError):
        return str(ts or "")
    return f"{moment:%b} {moment.day}, {moment:%H:%M} UTC"


def since_label(moment: datetime) -> str:
    return f"{moment:%b} {moment.day}, {moment:%H:%M} UTC"


def _link(url: str) -> str:
    return f" [link]({url})" if url else ""


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def as_json(envelope: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"


def report(envelope: Mapping[str, Any], only: str = "", channel: str = "") -> str:
    """`only` is "", "decisions" or "mentions"; `channel` limits the reply to one channel (expanded)."""
    blocks = [f"**{envelope['summary']}**"]
    shown = [c for c in envelope["by_channel"] if not channel or _matches(c, channel)]
    if only:
        shown = [c for c in shown if c[only]]
    for c in shown:
        blocks.append(_channel(c, only, expanded=bool(channel)))
    if len(blocks) == 1:
        blocks.append(_nothing(only))
    blocks.extend(f"_Note: {line}_" for line in envelope.get("coverage", []))
    return "\n\n".join(blocks)


def _matches(c: Mapping[str, Any], name: str) -> bool:
    wanted = name.casefold().lstrip("#")
    return c["channel"].casefold().lstrip("#") == wanted or c["channel_id"].casefold() == wanted


def _nothing(only: str) -> str:
    if only:
        return f"No {only} in the conversations I read."
    return "Nothing new in the conversations I read."


def _channel(c: Mapping[str, Any], only: str, expanded: bool) -> str:
    stats = f"{_count(c['message_count'], 'msg')}" + (f", {_count(c['thread_count'], 'thread')}" if c["thread_count"] else "")
    signals = [_count(len(c["mentions"]), "mention")] if c["mentions"] else []
    signals += [_count(len(c["decisions"]), "decision")] if c["decisions"] else []
    head = f"**{clean(c['channel'])}** ({stats})" + (f" - {', '.join(signals)}" if signals else "")
    lines = [head]
    if not c["summarized"]:
        lines.append(f"- {c['note'] or 'not summarized'}")
        return "\n".join(lines)
    if only != "decisions":
        lines += [_mention(m) for m in c["mentions"]]
    if only != "mentions":
        lines += [_decision(d) for d in c["decisions"]]
    if not only:
        lines += [_highlight(h, expanded) for h in c["highlights"]]
        if c.get("broadcasts"):
            lines.append(f"- {_count(c['broadcasts'], 'broadcast')} to everyone (@here / @channel)")
        if c.get("note"):
            lines.append(f"- _{c['note']}_")
    return "\n".join(lines)


def _mention(m: Mapping[str, Any]) -> str:
    kind = "Reply in your thread" if m.get("kind") == "thread_reply" else "Mention"
    return f"- {kind}: {clean(m['author'])}: \"{clean(m['quote'])}\" ({m.get('time') or when(m['ts'])}){_link(m['link'])}"


def _decision(d: Mapping[str, Any]) -> str:
    changed = f" - **later changed**: \"{clean(d['changed_quote'])}\"" if d.get("later_changed") else ""
    return (f"- Decision: {clean(d['decision'])}: \"{clean(d['quote'])}\" - {clean(d['decided_by'])} "
            f"({d.get('time') or when(d['ts'])}){_link(d['link'])}{changed}")


def _highlight(h: Mapping[str, Any], expanded: bool) -> str:
    quote = f": \"{clean(h['quote'])}\" - {clean(h['author'])}" if expanded else ""
    return f"- {clean(h['line'])}{quote}{_link(h['link'])}"


def channel_names(envelope: Mapping[str, Any]) -> Sequence[str]:
    return [c["channel"] for c in envelope.get("by_channel", [])]
