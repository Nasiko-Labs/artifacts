"""Pure: one pasted message into traces and code blocks.

Code blocks are found first: a fenced block (unless it holds a trace), or an unfenced block under a header line
(`# file: app/orders.py`, `// file: src/a.ts`, `--- app/orders.py`). A block may declare its first line number
(`# lines 40-70`). What is left is split into traces at start markers; only the last 3 traces are kept, and the
last one is primary unless the user's words point at the first."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TRACES = 3
MAX_BLOCK_CHARS = 12_000
MAX_CODE_CHARS = 40_000

_FENCE = re.compile(r"^```[ \t]*(?P<info>[^\n`]*)\n(?P<body>.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_HEADER = re.compile(r"^\s*(?:(?:#|//|--)\s*file:\s*(?P<a>\S+)|--- (?:a/)?(?P<b>[\w./\\-]+\.\w+))[^\n]*$",
                     re.IGNORECASE)
_LINES = re.compile(r"\blines?\s+(?P<start>\d+)\s*(?:-|–|to)\s*(?P<end>\d+)", re.IGNORECASE)
_OFFSET_LINE = re.compile(r"^\s*(?:#|//|--|/\*)?\s*lines?\s+\d+\s*(?:-|–|to)\s*\d+\s*(?:\*/)?\s*$", re.IGNORECASE)
_PATH = re.compile(r"[\w./\\-]*\w\.(?:py|js|mjs|cjs|ts|tsx|jsx|java|kt|go|rs|rb|cs|php|scala|swift)\b")
_CHAIN = re.compile(r"During handling of the above exception|The above exception was the direct cause|Caused by:")
_STARTS = (
    re.compile(r"^\s*Traceback \(most recent call last\):"),
    re.compile(r"^Exception in thread "),
    re.compile(r"^(?:panic|fatal error): "),
    re.compile(r"^thread '[^']*' panicked at"),
    re.compile(r"^Unhandled exception\."),
    re.compile(r"^\S+:\d+:in [`'][^'`]+'"),
)
_GOROUTINE = re.compile(r"^goroutine \d+ \[")
_HEAD_WITH_AT = re.compile(r"^(?:Uncaught )?[\w.$]*(?:Error|Exception)\b")
_AT_LINE = re.compile(r"^\s+at \S")
_FIRST = re.compile(r"\bfirst (?:error|one|trace|exception|stack ?trace|traceback)\b", re.IGNORECASE)


@dataclass(frozen=True)
class CodeBlock:
    path: str                 # "" when nothing labels it
    lines: tuple[str, ...]
    start: int = 1
    explicit_start: bool = False
    truncated: bool = False

    def to_dict(self) -> dict:
        return {"path": self.path, "lines": list(self.lines), "start": self.start,
                "explicit_start": self.explicit_start, "truncated": self.truncated}

    @staticmethod
    def from_dict(data: dict) -> CodeBlock:
        return CodeBlock(str(data.get("path") or ""), tuple(data.get("lines") or ()), int(data.get("start") or 1),
                         bool(data.get("explicit_start")), bool(data.get("truncated")))


@dataclass(frozen=True)
class Paste:
    words: str                       # the user's own words outside traces and code (the request)
    traces: tuple[str, ...]          # kept traces, oldest first
    blocks: tuple[CodeBlock, ...]
    primary: int = 0                 # index into traces
    skipped_traces: int = 0
    notes: tuple[str, ...] = ()


def split(text: str) -> Paste:
    rest, blocks, notes = _code_blocks(text or "")
    words, traces = _split_traces(rest)
    skipped = max(0, len(traces) - MAX_TRACES)
    kept = tuple(traces[-MAX_TRACES:])
    primary = 0 if _FIRST.search(words) else max(0, len(kept) - 1)
    return Paste(words.strip(), kept, blocks, primary, skipped, notes)


def _code_blocks(text: str) -> tuple[str, tuple[CodeBlock, ...], tuple[str, ...]]:
    raw: list[tuple[str, list[str], str]] = []  # (path, lines, header line)

    def take_fence(match: re.Match) -> str:
        body = match["body"]
        if any(rx.search(line) for line in body.splitlines() for rx in _STARTS) or _looks_like_trace(body):
            return body
        before = text[:match.start()].rstrip().splitlines()
        previous = before[-1] if before else ""
        header = _HEADER.match(previous)
        path = _path_in(match["info"]) or (header and (header["a"] or header["b"])) or _path_in(previous)
        raw.append((path or "", body.rstrip("\n").splitlines(), previous))
        return ""

    rest = _FENCE.sub(take_fence, text)
    rest_lines, i = [], 0
    lines = rest.splitlines()
    while i < len(lines):
        header = _HEADER.match(lines[i])
        if not header:
            rest_lines.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not _HEADER.match(lines[j]) and not _is_start(lines, j):
            j += 1
        if any(line.strip() for line in lines[i + 1:j]):
            raw.append((header["a"] or header["b"], lines[i + 1:j], lines[i]))
        i = j
    return "\n".join(rest_lines), *_finish(raw)


def _finish(raw: list[tuple[str, list[str], str]]) -> tuple[tuple[CodeBlock, ...], tuple[str, ...]]:
    blocks: list[CodeBlock] = []
    notes: list[str] = []
    total = 0
    for path, lines, header in raw:
        start, explicit = 1, False
        offset = _LINES.search(header)
        if lines and _OFFSET_LINE.match(lines[0]):
            offset, lines = _LINES.search(lines[0]), lines[1:]
        if offset:
            start, explicit = max(1, int(offset["start"])), True
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        body = "\n".join(lines)
        truncated = len(body) > MAX_BLOCK_CHARS
        if total + min(len(body), MAX_BLOCK_CHARS) > MAX_CODE_CHARS:
            notes.append(f"Skipped the code block for {path or 'an unlabelled snippet'}: over the "
                         f"{MAX_CODE_CHARS:,}-character code limit.")
            continue
        if truncated:
            lines = body[:MAX_BLOCK_CHARS].splitlines()
            notes.append(f"Read only the first {MAX_BLOCK_CHARS:,} characters of {path or 'a snippet'}.")
        total += min(len(body), MAX_BLOCK_CHARS)
        blocks.append(CodeBlock(path, tuple(lines), start, explicit, truncated))
    return tuple(blocks), tuple(notes)


def _path_in(text: str) -> str:
    match = _PATH.search(text or "")
    return match.group(0) if match else ""


def _looks_like_trace(body: str) -> bool:
    lines = body.splitlines()
    return any(_HEAD_WITH_AT.match(line) for line in lines) and sum(bool(_AT_LINE.match(x)) for x in lines) >= 1


def _is_start(lines: list[str], i: int) -> bool:
    line = lines[i]
    if any(rx.match(line) for rx in _STARTS):
        return True
    if _HEAD_WITH_AT.match(line):
        following = next((x for x in lines[i + 1:] if x.strip()), "")
        return bool(_AT_LINE.match(following))
    return False


def _split_traces(text: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    segments: list[list[str]] = []
    words: list[str] = []
    for i, line in enumerate(lines):
        if _starts_segment(lines, i, segments):
            segments.append([line])
        elif segments:
            segments[-1].append(line)
        else:
            words.append(line)
    if not segments:
        return text, ([text] if text.strip() else [])
    return "\n".join(words), ["\n".join(s).strip() for s in segments]


def _starts_segment(lines: list[str], i: int, segments: list[list[str]]) -> bool:
    line = lines[i]
    if _GOROUTINE.match(line):
        current = segments[-1] if segments else []
        return not (current and re.match(r"^(?:panic|fatal error): |^goroutine ", current[0]))
    if not _is_start(lines, i):
        return False
    previous = [x for x in lines[max(0, i - 3):i] if x.strip()]
    return not (previous and _CHAIN.search(previous[-1]))  # a chained traceback continues the same trace


_CODEISH = re.compile(r"[(){};=\[\]]|^\s{2,}\S|^\s*(?:def|class|return|if|for|while|import|from|func|fn|let|const|"
                      r"var|public|private|static|async|await)\b")
LOOSE_MIN_LINES = 3


def loose_block(text: str) -> CodeBlock | None:
    """Unfenced, unlabelled code pasted as a follow-up ("lines 40-70 of OrderRepository.java:" + code): a block
    labelled from the prose line before it, or None when the message does not look like code."""
    lines = [line for line in (text or "").splitlines()]
    while lines and not lines[0].strip():
        lines = lines[1:]
    if not lines:
        return None
    label = lines[0] if not _CODEISH.search(lines[0]) or (_PATH.search(lines[0]) and _LINES.search(lines[0])) else ""
    body = lines[1:] if label else lines
    code_lines = [x for x in body if x.strip()]
    if len(code_lines) < LOOSE_MIN_LINES or sum(bool(_CODEISH.search(x)) for x in code_lines) * 2 < len(code_lines):
        return None
    offset = _LINES.search(label)
    start = max(1, int(offset["start"])) if offset else 1
    return CodeBlock(_path_in(label), tuple(body[:MAX_BLOCK_CHARS]), start, bool(offset))
