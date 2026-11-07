"""Rendering in code: markdown only, no raw HTML, no images, URLs only from tool output. Chat markup
(`<@U1>`, `<#C1|infra>`, `<https://x|label>`) is flattened and any remaining angle bracket replaced, so
nothing from a chat message can become HTML."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date

QUOTE_CHARS = 300
_LINK_LABEL = re.compile(r"<(https?://[^|>\s]+)\|([^>]*)>")
_BARE_LINK = re.compile(r"<(https?://[^>\s]+)>")
_CHANNEL_REF = re.compile(r"<#[A-Z0-9]+\|([^>]*)>")
_USER_REF = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_SPECIAL = re.compile(r"<!(channel|here|everyone)[^>]*>")


def clean(text: str, limit: int = QUOTE_CHARS) -> str:
    out = _LINK_LABEL.sub(r"\2 (\1)", text or "")
    out = _BARE_LINK.sub(r"\1", out)
    out = _CHANNEL_REF.sub(r"#\1", out)
    out = _USER_REF.sub(r"@\1", out)
    out = _SPECIAL.sub(r"@\1", out)
    out = " ".join(out.replace("<", "‹").replace(">", "›").replace("`", "'").split())
    return out if len(out) <= limit else out[:limit] + "…"


def day(iso: str, today: date | None) -> str:
    try:
        value = date.fromisoformat(iso)
    except ValueError:
        return iso
    label = f"{value.day} {value.strftime('%b')}"
    return label if today is not None and value.year == today.year else f"{label} {value.year}"


def answer_line(n: int, a: Mapping, today: date | None) -> str:
    flags = []
    if a["stale"]:
        flags.append("may be out of date")
    head = f"{n}. {a['match'].capitalize()}, {day(a['answered_at'], today)}, {a['channel']}"
    if flags:
        head += f" ({', '.join(flags)})"
    line = f"{head} - {clean(a['answered_by'], 60)} answered \"{clean(a['answer'])}\" to \"{clean(a['question'])}\""
    if a["acknowledged"]:
        line += " (acknowledged)"
    if a.get("link"):
        line += f" [link]({a['link']})"
    return line


def searched_line(env: Mapping) -> str:
    searched = env["searched"]
    channels = list(searched["channels"])
    months = searched["months"]
    span = f"{months} month{'s' if months != 1 else ''}"
    if not channels:
        return f"Searched your chat history, {span}."
    if len(channels) <= 3:
        return f"Searched {', '.join(channels)}, {span}."
    return f"Searched {', '.join(channels[:2])} and {len(channels) - 2} other channels, {span}."


def report(env: Mapping, today: date | None, numbering_from: int = 1) -> str:
    shown = env["prior_answers"]
    parts: list[str] = []
    if shown:
        strong = sum(1 for a in shown if a["match"] == "strong")
        partial = len(shown) - strong
        counts = ", ".join(x for x in (f"{strong} strong" if strong else "", f"{partial} partial" if partial else "") if x)
        title = "Probably answered before" if strong else "Possibly answered before"
        parts.append(f"**{title} ({counts})**")
        parts.append("\n".join(answer_line(numbering_from + i, a, today) for i, a in enumerate(shown)))
        parts.append(searched_line(env))
    else:
        parts.append("**No prior answer found.**")
        parts.append(searched_line(env) + " Nothing there answers this question.")
    parts += [f"_Note: {clean(n, 400)}_" for n in env["coverage"]]
    if shown:
        parts.append("Say 'expand 1' to see a whole thread, or 'more' for other matches.")
    if env.get("restated_question"):
        parts.append(f"If you still need to ask, here is your question to copy:\n\n> {clean(env['restated_question'], 1600)}")
    return "\n\n".join(parts)


def thread_view(n: int, answer: Mapping, messages: Sequence[Mapping], today: date | None) -> str:
    head = f"**Thread {n}, {answer['channel']}**"
    if answer.get("link"):
        head += f" [link]({answer['link']})"
    lines = [f"- **{clean(m['author'], 60)}** ({day(m['when'], today)}): {clean(m['text'], 1200)}" for m in messages]
    return "\n".join([head, *lines]) if lines else head + "\n(no messages stored)"


def as_json(env: Mapping) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"
