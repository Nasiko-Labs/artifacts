"""Pure: read a transcript tool result generically (no model involved) and cut it into chunks.

Accepted shapes, at any depth of the JSON:
- a list of segments, each with a speaker field, a text field and a start offset;
- a list of speaker blocks, each with a speaker field and a list of {start, text} sentences.
Offsets in milliseconds or seconds (or "mm:ss" strings). Consecutive segments of one speaker are merged
into one turn. A speaker is a customer only when it maps to an external participant of the call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from records import Call, Participant, Turn, mm_ss, text_of

_SPEAKER_KEYS = ("speaker", "speakerId", "speaker_id", "speakerName", "speaker_name", "participant", "participantId",
                 "user", "name")
_TEXT_KEYS = ("text", "sentence", "content", "words", "utterance")
_START_KEYS = ("start", "startTime", "start_time", "startMs", "start_ms", "offset", "offset_s", "timestamp",
               "time", "start_seconds")
_SENTENCE_KEYS = ("sentences", "segments", "utterances", "lines")
_CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?$")
_HOURS_IN_SECONDS = 6 * 3600


@dataclass(frozen=True)
class _Segment:
    speaker: str
    text: str
    start: float | None
    start_key: str


def parse(raw_text: str, call: Call) -> tuple[Turn, ...] | None:
    """The call's turns, or None when the shape can't be read."""
    try:
        data = json.loads(raw_text)
    except ValueError:
        return None
    segments = _best(_scope(data, call.id))
    if not segments:
        return None
    scale = _scale(segments, call.duration_s)
    turns: list[Turn] = []
    for seg in segments:
        offset = int((seg.start or 0) * scale)
        if turns and turns[-1].speaker == _display(seg.speaker, call):
            last = turns[-1]
            turns[-1] = Turn(last.speaker, last.external, last.offset_s, f"{last.text} {seg.text}")
        else:
            turns.append(Turn(_display(seg.speaker, call), _is_external(seg.speaker, call), offset, seg.text))
    return tuple(turns)


def _scope(data: Any, call_id: str) -> Any:
    """When a result holds several calls, the object of this call."""
    if isinstance(data, Mapping):
        for value in data.values():
            if isinstance(value, list):
                own = [v for v in value if isinstance(v, Mapping)
                       and any(text_of(v.get(k)) == call_id for k in ("callId", "call_id", "id"))]
                if own:
                    return own[0]
    return data


def _best(data: Any) -> list[_Segment]:
    """The longest segment list found anywhere in the JSON."""
    best: list[_Segment] = []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            found = _segments(node)
            best = found if len(found) > len(best) else best
            stack.extend(node)
        elif isinstance(node, Mapping):
            stack.extend(node.values())
    return best


def _segments(items: list) -> list[_Segment]:
    out: list[_Segment] = []
    for item in items:
        if not isinstance(item, Mapping):
            return []
        speaker = _first(item, _SPEAKER_KEYS)
        if speaker is None:
            return []
        nested = next((item[k] for k in _SENTENCE_KEYS if isinstance(item.get(k), list)), None)
        rows = nested if nested is not None else [item]
        for row in rows:
            text = _first(row, _TEXT_KEYS) if isinstance(row, Mapping) else None
            if not text:
                continue
            key = next((k for k in _START_KEYS if k in row), "")
            out.append(_Segment(speaker, text, _seconds_or_raw(row.get(key)), key))
    return out


def _first(item: Mapping, keys: Sequence[str]) -> str | None:
    value = next((item[k] for k in keys if isinstance(item.get(k), (str, int)) and not isinstance(item.get(k), bool)),
                 None)
    return text_of(value) or None if value is not None else None


def _seconds_or_raw(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _CLOCK.match(str(value).strip())
    if match:
        return -(int(match.group(1) or 0) * 3600 + int(match.group(2)) * 60 + int(match.group(3)))  # marked
    try:
        return float(value)
    except ValueError:
        return None


def _scale(segments: Sequence[_Segment], duration_s: float | None) -> float:
    """Multiplier to seconds. Clock strings are stored negative (already seconds)."""
    if any(s.start is not None and s.start < 0 for s in segments):
        return -1.0
    if any("ms" in s.start_key.casefold() for s in segments):
        return 0.001
    top = max((s.start or 0.0) for s in segments)
    limit = duration_s * 2 if duration_s else _HOURS_IN_SECONDS
    return 0.001 if top > limit else 1.0


def _who(speaker: str, call: Call) -> Participant | None:
    key = speaker.casefold()
    return next((p for p in call.participants
                 if key in {p.speaker_id.casefold(), p.name.casefold(), p.email.casefold()} - {""}), None)


def _display(speaker: str, call: Call) -> str:
    person = _who(speaker, call)
    return (person.name or person.email) if person else speaker


def _is_external(speaker: str, call: Call) -> bool:
    person = _who(speaker, call)
    return bool(person and person.external)


@dataclass(frozen=True)
class Chunking:
    chunks: tuple[str, ...]
    total: int  # parts before the cap


def chunk(turns: Sequence[Turn], chunk_chars: int, max_chunks: int) -> Chunking:
    """Customer turns plus the one turn before each (context), packed into chunks; over the cap, the
    first, middle and last parts are kept."""
    lines = [_line(t, chunk_chars) for i, t in enumerate(turns)
             if t.external or (i + 1 < len(turns) and turns[i + 1].external)]
    parts: list[str] = []
    for line in lines:
        if parts and len(parts[-1]) + len(line) + 1 <= chunk_chars:
            parts[-1] = f"{parts[-1]}\n{line}"
        else:
            parts.append(line)
    if len(parts) <= max_chunks:
        return Chunking(tuple(parts), len(parts))
    picks = sorted({0, len(parts) // 2, len(parts) - 1})[:max_chunks]
    return Chunking(tuple(parts[i] for i in picks), len(parts))


def _line(turn: Turn, limit: int) -> str:
    role = "customer" if turn.external else "us"
    return f"[{mm_ss(turn.offset_s)}] {turn.speaker} ({role}): {turn.text}"[:limit]
