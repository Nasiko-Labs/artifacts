"""Pure: candidate threads pasted by the user (the fallback without a chat connection). One message per
line as `[time] name: text`; a blank line or a `#channel` line starts a new thread; the first line of a
thread is its question. Text before the first such line is the user's question. Times stay as written,
there are no links, and confidence is always low."""

from __future__ import annotations

import re

from models import Message, Thread

MIN_PASTED_LINES = 2
MAX_PASTED_THREADS = 8
NOTE = "Checked {n} pasted thread{s} (best effort: times as written, no links)."
_LINE = re.compile(r"^\s*\[([^\]\n]{1,40})\]\s*([^:\n]{1,60}?)\s*:\s*(.+?)\s*$")
_CHANNEL_LINE = re.compile(r"^\s*#([\w.-]+)\s*:?\s*$")


def split(text: str) -> tuple[str, tuple[Thread, ...]]:
    """(the user's question, the pasted threads); no threads when fewer than 2 message lines."""
    lines = (text or "").splitlines()
    if sum(1 for line in lines if _LINE.match(line)) < MIN_PASTED_LINES:
        return text, ()
    first = next(n for n, line in enumerate(lines) if _LINE.match(line) or _CHANNEL_LINE.match(line))
    question = " ".join(line.strip() for line in lines[:first] if line.strip())
    blocks: list[tuple[str, list[tuple[str, str, str]]]] = []
    channel, current = "", []
    for line in lines[first:]:
        header, match = _CHANNEL_LINE.match(line), _LINE.match(line)
        if header or not line.strip():
            if current:
                blocks.append((channel, current))
            channel, current = (header.group(1) if header else channel), []
        elif match:
            current.append((match.group(1).strip(), match.group(2).strip(), match.group(3)))
        elif current:  # a wrapped line continues the previous message
            time, name, body = current[-1]
            current[-1] = (time, name, f"{body} {line.strip()}")
    if current:
        blocks.append((channel, current))
    return question, tuple(_thread(k, ch, msgs) for k, (ch, msgs) in enumerate(blocks[:MAX_PASTED_THREADS]))


def _thread(k: int, channel: str, rows: list[tuple[str, str, str]]) -> Thread:
    label = f"#{channel}" if channel else "pasted"
    messages = tuple(Message(channel_id="pasted", channel_name=channel, ts=str(n + 1), author=name, text=body,
                             time_label=time) for n, (time, name, body) in enumerate(rows))
    return Thread(key=f"pasted:{k + 1}", channel_id="pasted", channel_label=label, root_ts="1", root=messages[0],
                  replies=messages[1:], reply_count=len(messages) - 1)
