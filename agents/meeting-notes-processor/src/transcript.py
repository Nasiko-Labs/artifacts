"""Pure: a pasted transcript or raw notes, parsed into numbered turns in code.

Formats are tried in order; the first that yields at least MIN_TURNS turns wins:
  subtitle cues (VTT / SRT)         speaker from <v Name> or a leading "Name:", time from the cue start
  "[00:12:03] Name: text", "Name (12:03): text"
  "Name 00:12:03" on its own line   the text on the lines after it
  "00:12 Name" on its own line      the text on the lines after it
  "Name: text"
  raw notes                          every line or bullet is a turn by "Unknown"
Same-speaker turns within MERGE_GAP_SECS are merged; ids T1..Tn are given last."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from items import UNKNOWN, Turn
from timecode import show_time, to_secs

MIN_TURNS = 5
MERGE_GAP_SECS = 60
_NAME = r"[A-Z][\w.'’-]*(?: [A-Z0-9][\w.'’-]*){0,3}"
_TIME = r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"
LABELS = frozenset({"title", "date", "meeting", "subject", "attendees", "participants", "agenda", "note", "notes",
                    "action items", "actions", "action", "decisions", "decision", "time", "location", "summary",
                    "re", "topic", "next steps", "duration", "where", "when", "to", "from", "cc", "http", "https"})

_INLINE_TIME_FIRST = re.compile(rf"^\s*[\[(]?({_TIME})[\])]?\s*[-–]?\s*({_NAME})\s*[:\-–]\s+(.*)$")
_INLINE_NAME_FIRST = re.compile(rf"^\s*({_NAME})\s*[\[(]({_TIME})[\])]\s*:?\s*(.*)$")
_HEADER_NAME_TIME = re.compile(rf"^\s*({_NAME})\s+[\[(]?({_TIME})[\])]?\s*$")
_HEADER_TIME_NAME = re.compile(rf"^\s*[\[(]?({_TIME})[\])]?\s+({_NAME})\s*$")
_PLAIN = re.compile(rf"^\s*({_NAME})\s*:\s+(.*)$")
_META = re.compile(r"^\s*(title|meeting|subject|date)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_CUE_TIMING = re.compile(rf"^\s*({_TIME})\s*-->")
_VOICE = re.compile(r"<v(?:\.[\w.-]+)?\s+([^>]+)>")
_TAG = re.compile(r"</?[^>]{1,80}>")
_BULLET = re.compile(r"^\s*(?:[-*•‣◦]+|\d{1,3}[.)])\s+")


@dataclass(frozen=True)
class Parsed:
    turns: tuple[Turn, ...]
    fmt: str               # cues | labelled | notes | empty
    has_speakers: bool
    title: str = ""
    date: str = ""


def parse(text: str) -> Parsed:
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title, date = _meta(lines)
    body = [line for line in lines if not _META.match(line)]
    candidates: list[tuple[str, Callable[[list[str]], list[Turn]]]] = [
        ("labelled", _inline(_INLINE_TIME_FIRST, time_first=True)),
        ("labelled", _inline(_INLINE_NAME_FIRST, time_first=False)),
        ("labelled", _header(_HEADER_NAME_TIME, time_first=False)),
        ("labelled", _header(_HEADER_TIME_NAME, time_first=True)),
        ("labelled", _plain),
    ]
    if any(_CUE_TIMING.match(line) for line in body):
        candidates.insert(0, ("cues", _cues))
    for fmt, parser in candidates:
        turns = [t for t in parser(body) if t.text.strip()]
        if len(turns) >= MIN_TURNS:
            return Parsed(_number(_merge(turns)), fmt, True, title, date)
    notes = _notes(body)
    if not notes:
        return Parsed((), "empty", False, title, date)
    return Parsed(_number(notes), "notes", False, title, date)


def _meta(lines: list[str]) -> tuple[str, str]:
    title = date = ""
    for line in [x for x in lines if x.strip()][:6]:
        match = _META.match(line)
        if match and match.group(1).casefold() == "date":
            date = date or match.group(2)[:60]
        elif match:
            title = title or match.group(2)[:80]
    return title, date


def _is_name(name: str) -> bool:
    return name.strip().casefold() not in LABELS


def _turn(speaker: str, time: str, text: str) -> Turn:
    secs = to_secs(time) if time else -1
    shown = show_time(secs) if "." in time or "," in time else time.strip()
    return Turn(id="", speaker=speaker.strip(), time=shown, text=" ".join(text.split()), secs=secs)


def _continue(turns: list[Turn], line: str) -> None:
    """A line without a label continues the previous turn (lines before the first turn are preamble)."""
    if turns and line.strip():
        turns[-1] = replace(turns[-1], text=f"{turns[-1].text} {' '.join(line.split())}".strip())


def _inline(pattern: re.Pattern, time_first: bool) -> Callable[[list[str]], list[Turn]]:
    def run(lines: list[str]) -> list[Turn]:
        turns: list[Turn] = []
        for line in lines:
            match = pattern.match(line)
            time, name = (match.group(1), match.group(2)) if match and time_first else (
                (match.group(2), match.group(1)) if match else ("", ""))
            if match and _is_name(name):
                turns.append(_turn(name, time, match.group(3)))
            else:
                _continue(turns, line)
        return turns
    return run


def _header(pattern: re.Pattern, time_first: bool) -> Callable[[list[str]], list[Turn]]:
    def run(lines: list[str]) -> list[Turn]:
        turns: list[Turn] = []
        for line in lines:
            match = pattern.match(line)
            if match:
                time, name = (match.group(1), match.group(2)) if time_first else (match.group(2), match.group(1))
                if _is_name(name):
                    turns.append(_turn(name, time, ""))
                    continue
            _continue(turns, line)
        return turns
    return run


def _plain(lines: list[str]) -> list[Turn]:
    turns: list[Turn] = []
    for line in lines:
        match = _PLAIN.match(line)
        if match and _is_name(match.group(1)):
            turns.append(_turn(match.group(1), "", match.group(2)))
        else:
            _continue(turns, line)
    return turns


def _cues(lines: list[str]) -> list[Turn]:
    turns: list[Turn] = []
    speaker, time, buffer = UNKNOWN, "", []

    def flush() -> None:
        if time and buffer:
            turns.append(_turn(speaker, time, " ".join(buffer)))

    for line in lines:
        timing = _CUE_TIMING.match(line)
        if timing:
            flush()
            speaker, time, buffer = UNKNOWN, timing.group(1), []
        elif time and line.strip() and not (not buffer and line.strip().isdigit()):
            voice = _VOICE.search(line)
            text = _TAG.sub("", line).strip()
            plain = _PLAIN.match(text)
            if voice:
                speaker = voice.group(1).strip()
            elif plain and not buffer and _is_name(plain.group(1)):
                speaker, text = plain.group(1), plain.group(2)
            buffer.append(text)
        elif not line.strip():
            flush()
            time, buffer = "", []
    flush()
    return turns


def _notes(lines: list[str]) -> list[Turn]:
    texts = [_BULLET.sub("", line).strip() for line in lines]
    return [Turn(id="", speaker=UNKNOWN, time="", text=" ".join(t.split())) for t in texts if t]


def _merge(turns: list[Turn]) -> list[Turn]:
    merged: list[Turn] = []
    for turn in turns:
        last = merged[-1] if merged else None
        close = last is not None and (last.secs < 0 or turn.secs < 0 or 0 <= turn.secs - last.secs <= MERGE_GAP_SECS)
        if last is not None and close and turn.speaker == last.speaker and turn.speaker != UNKNOWN:
            merged[-1] = replace(last, text=f"{last.text} {turn.text}")
        else:
            merged.append(turn)
    return merged


def _number(turns: list[Turn]) -> tuple[Turn, ...]:
    return tuple(replace(t, id=f"T{n}") for n, t in enumerate(turns, start=1))
