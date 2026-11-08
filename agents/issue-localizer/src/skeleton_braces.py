"""Pure: declarations of brace languages (JS/TS, Go, Java/Kotlin/C#, Rust) by regex, spans by brace
matching that skips strings and comments (best effort). Without a body brace the span ends at the line
before the next declaration."""

from __future__ import annotations

import re
from collections.abc import Sequence

from skeleton_types import Symbol

_ID = r"[A-Za-z_$][\w$]*"
JS = (
    (re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*({_ID})\s*\("), "function"),
    (re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+({_ID})"), "class"),
    (re.compile(rf"^\s*(?:export\s+)?(?:const|let|var)\s+({_ID})\s*(?::[^=]+)?=\s*(?:async\s+)?"
                rf"(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|{_ID}\s*=>)"), "function"),
    (re.compile(rf"^\s+(?:public\s+|private\s+|protected\s+|static\s+|async\s+|readonly\s+|get\s+|set\s+)*"
                rf"(?!if\b|for\b|while\b|switch\b|catch\b|return\b|function\b)({_ID})\s*\([^;]*\)\s*(?::[^{{]+)?\{{"),
     "method"),
)
GO = (
    (re.compile(rf"^func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s*({_ID})\s*\("), "method"),
    (re.compile(rf"^func\s+({_ID})\s*[\[(]"), "function"),
    (re.compile(rf"^type\s+({_ID})\s+(?:struct|interface)\b"), "type"),
)
JVM = (
    (re.compile(rf"^\s*(?:(?:public|private|protected|internal|static|final|abstract|sealed|open|data|partial)\s+)*"
                rf"(?:class|interface|enum|object|record|struct)\s+({_ID})"), "class"),
    (re.compile(rf"^\s*(?:(?:public|private|protected|internal|static|final|abstract|override|virtual|async|"
                rf"synchronized|suspend|open)\s+)+(?:[\w<>\[\],.? ]+\s+)?({_ID})\s*\("), "method"),
    (re.compile(rf"^\s*fun\s+(?:<[^>]+>\s*)?(?:\w+\.)?({_ID})\s*\("), "function"),
)
RUST = (
    (re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+({_ID})"), "function"),
    (re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+({_ID})"), "type"),
    (re.compile(rf"^\s*impl(?:<[^>]*>)?\s+(?:{_ID}(?:<[^>]*>)?\s+for\s+)?({_ID})"), "impl"),
)


def build(text: str, patterns: Sequence[tuple[re.Pattern, str]], qualify_methods: bool = True) -> tuple[Symbol, ...]:
    lines = text.split("\n")
    heads = [(i, _match(line, patterns)) for i, line in enumerate(lines)]
    heads = [(i, m) for i, m in heads if m is not None]
    symbols: list[Symbol] = []
    for n, (i, (name, kind)) in enumerate(heads):
        fallback = heads[n + 1][0] - 1 if n + 1 < len(heads) else len(lines) - 1
        end = _brace_end(lines, i)
        end = fallback if end is None else end
        symbols.append(Symbol(name, kind, i + 1, max(i, end) + 1, lines[i].strip()[:160]))
    return tuple(_qualify(symbols) if qualify_methods else symbols)


def _match(line: str, patterns: Sequence[tuple[re.Pattern, str]]) -> tuple[str, str] | None:
    for pattern, kind in patterns:
        found = pattern.match(line)
        if found:
            groups = [g for g in found.groups() if g]
            name = ".".join(groups) if kind == "method" and len(groups) == 2 else groups[-1]
            return name, kind
    return None


def _qualify(symbols: list[Symbol]) -> list[Symbol]:
    """A method inside a class span is named `Class.method`."""
    out = []
    for s in symbols:
        owner = next((c for c in symbols if c.kind == "class" and c.start < s.start <= c.end), None)
        named = f"{owner.name}.{s.name}" if owner and s.kind == "method" and "." not in s.name else s.name
        out.append(Symbol(named, s.kind, s.start, s.end, s.signature))
    return out


def _brace_end(lines: list[str], start: int, lookahead: int = 3) -> int | None:
    """Index of the line closing the first `{` opened on or just after `start`, else None."""
    depth, opened, in_block = 0, False, False
    for idx in range(start, len(lines)):
        if not opened and idx > start + lookahead:
            return None
        depth, opened, in_block = _scan(lines[idx], depth, opened, in_block)
        if opened and depth <= 0:
            return idx
    return None


def _scan(line: str, depth: int, opened: bool, in_block: bool) -> tuple[int, bool, bool]:
    i, quote = 0, ""
    while i < len(line):
        two = line[i:i + 2]
        ch = line[i]
        if in_block:
            in_block, i = (False, i + 2) if two == "*/" else (True, i + 1)
            continue
        if quote:
            i, quote = (i + 2, quote) if ch == "\\" else (i + 1, "" if ch == quote else quote)
            continue
        if two == "//":
            break
        if two == "/*":
            in_block, i = True, i + 2
            continue
        if ch in "\"'`":
            quote = ch
        elif ch == "{":
            depth, opened = depth + 1, True
        elif ch == "}":
            depth -= 1
        i += 1
    return depth, opened, in_block
