"""Pure: shrink a large tool result to what the model needs, keeping error anchors.

A result under the cap passes unchanged. A larger one is split into lines (a JSON body is re-indented
first, so it has lines), ANSI codes are stripped, and lines are kept by priority until the cap: the
anchor lines (error markers), the head (first 20 lines), the tail (last 40), then the ±10 lines around
each anchor. Repeats that differ only by timestamps or numbers are kept once. The kept lines are shown
in their original order with gap markers. The shown text is never rewritten, so a quote the model
copies from it is still found in the raw output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from fence import FencedGateway
from jsonutil import parse_json
from nasiko_mcp import NasikoMcpClient

DEFAULT_CAP = 6_000
CHANGES_CAP = 8_000
HEAD_LINES = 20
TAIL_LINES = 40
CONTEXT_LINES = 10
MAX_LINE_CHARS = 400

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ANCHOR = re.compile(r"\b(error|errors|exception|fatal|panic|timeout|timed out|traceback|critical|failed)\b",
                    re.IGNORECASE)
_VOLATILE = re.compile(r"\d{4}-\d{2}-\d{2}[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?|\b[0-9a-f]{8,}\b|\d+", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def excerpt(text: str, cap: int = DEFAULT_CAP) -> str:
    if len(text) <= cap:
        return text
    lines = _lines(text)
    picked = _pick(lines, cap)
    return _join(lines, picked)[:cap]


def _lines(text: str) -> list[str]:
    data = parse_json(text)
    body = json.dumps(data, indent=1, ensure_ascii=False) if isinstance(data, (dict, list)) else text
    return [line[:MAX_LINE_CHARS] for line in strip_ansi(body).splitlines()]


def _pick(lines: list[str], cap: int) -> list[int]:
    anchors = [i for i, line in enumerate(lines) if ANCHOR.search(line)]
    head = range(min(HEAD_LINES, len(lines)))
    tail = range(max(len(lines) - TAIL_LINES, 0), len(lines))
    context = [j for distance in range(1, CONTEXT_LINES + 1) for i in anchors for j in (i - distance, i + distance)
               if 0 <= j < len(lines)]
    chosen: dict[int, None] = {}
    seen_keys: set[str] = set()
    budget = cap - 200  # room for gap markers
    for index in (*anchors, *head, *tail, *context):
        if index in chosen:
            continue
        key = _VOLATILE.sub("#", lines[index]).strip()
        if key in seen_keys and key:
            continue
        cost = len(lines[index]) + 1
        if cost > budget:
            continue
        chosen[index] = None
        seen_keys.add(key)
        budget -= cost
    return sorted(chosen)


def _join(lines: list[str], picked: list[int]) -> str:
    out: list[str] = []
    previous = -1
    for index in picked:
        if index != previous + 1:
            out.append(f"[... {index - previous - 1} lines omitted ...]")
        out.append(lines[index])
        previous = index
    if previous != len(lines) - 1:
        out.append(f"[... {len(lines) - previous - 1} lines omitted ...]")
    return "\n".join(out)


def ExcerptingGateway(gateway: NasikoMcpClient, cap: int = DEFAULT_CAP) -> FencedGateway:
    """A FencedGateway whose model-facing text is the excerpt; `seen_text` keeps the raw output."""
    shape: Callable[[str], str] = lambda body: excerpt(body, cap)
    return FencedGateway(gateway, shape)
