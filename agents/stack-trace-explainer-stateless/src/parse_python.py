"""Pure: a Python traceback into blocks. Python prints frames oldest first; they are reversed here so the
innermost frame comes first. A chained traceback (`During handling…` / `The above exception was the direct
cause…`) is split into blocks; the first block is the root cause, the last the one the program died with."""

from __future__ import annotations

import re

from trace_model import Block, Frame, Trace

SIGNATURE = re.compile(r"^\s*Traceback \(most recent call last\):", re.MULTILINE)
_CHAIN = re.compile(r"^\s*(?:During handling of the above exception, another exception occurred:|"
                    r"The above exception was the direct cause of the following exception:)\s*$", re.MULTILINE)
_FRAME = re.compile(r'^\s*File "(?P<file>.+)", line (?P<line>\d+)(?:, in (?P<func>.+))?\s*$')
_REPEATED = re.compile(r"^\s*\[Previous line repeated (\d+) more times?\]\s*$")
_EXCEPTION = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<message>.*))?$")


def detect(text: str) -> bool:
    return bool(SIGNATURE.search(text))


def parse(text: str) -> Trace | None:
    pieces = [p for p in _CHAIN.split(text) if SIGNATURE.search(p)]
    blocks: list[Block] = []
    omitted = 0
    for piece in pieces:
        block, repeats = _block(piece)
        blocks.append(block)
        omitted += repeats
    if not blocks:
        return None
    return Trace("python", tuple(blocks), text, root=0, surface=len(blocks) - 1, omitted_repeats=omitted)


def _block(piece: str) -> tuple[Block, int]:
    lines = piece[SIGNATURE.search(piece).end():].splitlines()
    frames: list[Frame] = []
    omitted = 0
    exception = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        frame = _FRAME.match(line)
        if frame:
            source, step = _source_line(lines, i)
            frames.append(Frame(frame["file"], int(frame["line"]), (frame["func"] or "").strip(), source=source))
            exception = ""  # the exception line is the first unindented line after the last frame
            i += step
            continue
        repeated = _REPEATED.match(line)
        if repeated and frames:
            count = int(repeated.group(1))
            omitted += count
            last = frames[-1]
            frames[-1] = Frame(last.file, last.line, last.func, last.col, last.source, last.origin,
                               last.repeat + count)
        elif line.strip() and not line[:1].isspace() and not exception:
            exception = line.strip()
        i += 1
    exc_type, message = split_exception(exception)
    return Block(exc_type, message, tuple(reversed(frames))), omitted


def _source_line(lines: list[str], i: int) -> tuple[str, int]:
    """The indented source line under a frame, when the runtime printed one (and the caret line after it)."""
    if i + 1 < len(lines) and lines[i + 1][:1].isspace() and not _FRAME.match(lines[i + 1]) \
            and not _REPEATED.match(lines[i + 1]) and lines[i + 1].strip():
        step = 2
        if i + 2 < len(lines) and set(lines[i + 2].strip()) <= set("^~ ") and lines[i + 2].strip():
            step = 3
        return lines[i + 1].strip(), step
    return "", 1


def split_exception(line: str) -> tuple[str, str]:
    match = _EXCEPTION.match(line or "")
    if match:
        return match["type"], (match["message"] or "").strip()
    return ("Exception", line.strip()) if line.strip() else ("Exception", "")
