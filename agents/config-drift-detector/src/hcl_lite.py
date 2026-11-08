"""Pure: a literal-only HCL reader for Terraform files and `terraform state show` output.

Supported: blocks with labels (`resource "t" "n" { ... }`), nested blocks (repeated blocks become a list),
attributes whose value is a string, number, bool, null, list or object of those, and `#`, `//`, `/* */`
comments. Anything else (`var.x`, `local.y`, function calls, `${...}` interpolation, heredocs, conditionals)
is kept as `Unresolved`, so the comparison skips and counts it instead of guessing. Every attribute path
gets its 1-based line number.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any

Path = tuple[str | int, ...]
_IDENT = re.compile(r"[A-Za-z_][\w.\-]*")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_HEREDOC = re.compile(r"<<-?([A-Za-z_]\w*)")
_KEYWORDS = {"true": True, "false": False, "null": None}


@dataclass(frozen=True)
class Unresolved:
    text: str


@dataclass(frozen=True)
class Block:
    kind: str
    labels: tuple[str, ...]
    body: dict
    line: int
    lines: dict = field(default_factory=dict)  # path inside the body → line

    @property
    def address(self) -> str:
        return ".".join(self.labels) if self.kind == "resource" else ".".join((self.kind, *self.labels))


class _Scanner:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self._newlines = [m.start() for m in re.finditer("\n", text)]

    def line(self, at: int | None = None) -> int:
        return bisect.bisect_right(self._newlines, (self.i if at is None else at) - 1) + 1

    def peek(self, n: int = 1) -> str:
        return self.s[self.i:self.i + n]

    def eof(self) -> bool:
        return self.i >= len(self.s)

    def skip(self, newlines: bool = True) -> None:
        while not self.eof():
            c = self.s[self.i]
            if c in " \t\r" or (newlines and c == "\n"):
                self.i += 1
            elif c == "#" or self.peek(2) == "//":
                end = self.s.find("\n", self.i)
                self.i = len(self.s) if end == -1 else end
            elif self.peek(2) == "/*":
                end = self.s.find("*/", self.i + 2)
                self.i = len(self.s) if end == -1 else end + 2
            else:
                return

    def ident(self) -> str:
        m = _IDENT.match(self.s, self.i)
        if not m:
            return ""
        self.i = m.end()
        return m.group(0)

    def string(self) -> str | Unresolved:
        start, self.i, out = self.i, self.i + 1, []
        while not self.eof() and self.s[self.i] != '"':
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < len(self.s):
                nxt = self.s[self.i + 1]
                out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                self.i += 2
                continue
            out.append(c)
            self.i += 1
        self.i += 1
        raw = self.s[start:self.i]
        return Unresolved(raw) if "${" in raw or "%{" in raw else "".join(out)

    def raw_until_end(self) -> Unresolved:
        """Consume an expression this reader does not evaluate, up to the end of its value."""
        start, depth = self.i, 0
        while not self.eof():
            c = self.s[self.i]
            if c == '"':
                self.string()
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif (c == "\n" or c == ",") and depth == 0:
                break
            self.i += 1
        return Unresolved(self.s[start:self.i].strip())


def parse(text: str) -> tuple[Block, ...]:
    """Every top-level block in the text (resources, data sources, modules, variables...)."""
    sc = _Scanner(text or "")
    blocks: list[Block] = []
    while True:
        sc.skip()
        if sc.eof():
            break
        start = sc.i
        kind = sc.ident()
        if not kind:
            _skip_line(sc)
            continue
        labels = _labels(sc)
        sc.skip(newlines=False)
        if sc.peek() == "{":
            line = sc.line(start)
            sc.i += 1
            lines: dict = {}
            body = _body(sc, (), lines)
            blocks.append(Block(kind, tuple(labels), body, line, lines))
        else:
            _skip_line(sc)
    return tuple(blocks)


def parse_attributes(text: str) -> tuple[dict, dict]:
    """A file of bare attributes (`.tfvars`): the body and its line map."""
    sc, lines = _Scanner(text or ""), {}
    return _body(sc, (), lines, top=True), lines


def parse_value(text: str) -> Any:
    """One literal value as written in plan output (`"a"`, `5`, `["x", "y"]`); otherwise `Unresolved`."""
    sc = _Scanner((text or "").strip())
    value = _expr(sc, (), {})
    sc.skip()
    return value if sc.eof() else Unresolved(text.strip())


def _labels(sc: _Scanner) -> list[str]:
    labels = []
    while True:
        sc.skip(newlines=False)
        if sc.peek() == '"':
            value = sc.string()
            labels.append(value.text if isinstance(value, Unresolved) else value)
        elif _IDENT.match(sc.s, sc.i) and not sc.s.startswith("=", sc.i):
            pos = sc.i
            word = sc.ident()
            sc.skip(newlines=False)
            if sc.peek() not in ('"', "{") and not _IDENT.match(sc.s, sc.i):
                sc.i = pos
                return labels
            labels.append(word)
        else:
            return labels


def _skip_line(sc: _Scanner) -> None:
    end = sc.s.find("\n", sc.i)
    sc.i = len(sc.s) if end == -1 else end + 1


def _body(sc: _Scanner, path: Path, lines: dict, top: bool = False) -> dict:
    out: dict = {}
    while True:
        sc.skip()
        if sc.eof():
            return out
        if sc.peek() == "}" and not top:
            sc.i += 1
            return out
        start = sc.i
        key = sc.ident() if sc.peek() != '"' else _plain(sc.string())
        if not key:
            _skip_line(sc)
            continue
        sc.skip(newlines=False)
        if sc.peek() in ("=", ":"):
            sc.i += 1
            lines[(*path, key)] = sc.line(start)
            out[key] = _expr(sc, (*path, key), lines)
            continue
        _labels(sc)
        sc.skip(newlines=False)
        if sc.peek() != "{":
            _skip_line(sc)
            continue
        sc.i += 1
        items = out.setdefault(key, [])
        if not isinstance(items, list):
            items = out[key] = []
        sub = (*path, key, len(items))
        lines[sub] = sc.line(start)
        lines.setdefault((*path, key), sc.line(start))
        items.append(_body(sc, sub, lines))


def _plain(value: Any) -> str:
    return value.text if isinstance(value, Unresolved) else str(value)


def _expr(sc: _Scanner, path: Path, lines: dict) -> Any:
    sc.skip(newlines=False)
    c = sc.peek()
    if c == '"':
        value = sc.string()
    elif c == "[":
        value = _list(sc, path, lines)
    elif c == "{":
        value = _object(sc, path, lines)
    elif m := _HEREDOC.match(sc.s, sc.i):
        return _heredoc(sc, m.group(1))
    elif m := _NUMBER.match(sc.s, sc.i):
        sc.i = m.end()
        number = float(m.group(0))
        value = int(number) if number.is_integer() and not any(x in m.group(0) for x in ".eE") else number
    elif m := _IDENT.match(sc.s, sc.i):
        if m.group(0) not in _KEYWORDS:
            return sc.raw_until_end()
        sc.i = m.end()
        value = _KEYWORDS[m.group(0)]
    else:
        return sc.raw_until_end()
    return value if _at_end(sc) else Unresolved(_merge(value, sc.raw_until_end()))


def _merge(value: Any, rest: Unresolved) -> str:
    return f"{value!r} {rest.text}".strip()


def _at_end(sc: _Scanner) -> bool:
    pos = sc.i
    sc.skip(newlines=False)
    done = sc.eof() or sc.peek() in ("\n", ",", "]", "}", ")")
    if not done:
        sc.i = pos
    return done


def _heredoc(sc: _Scanner, marker: str) -> Unresolved:
    start = sc.i
    _skip_line(sc)
    while not sc.eof():
        end = sc.s.find("\n", sc.i)
        line = sc.s[sc.i: len(sc.s) if end == -1 else end]
        sc.i = len(sc.s) if end == -1 else end
        if line.strip() == marker:
            break
        sc.i += 1
    return Unresolved(sc.s[start:sc.i])


def _list(sc: _Scanner, path: Path, lines: dict) -> list:
    sc.i += 1
    items: list = []
    while True:
        sc.skip()
        if sc.eof():
            return items
        if sc.peek() == "]":
            sc.i += 1
            return items
        if sc.peek() == ",":
            sc.i += 1
            continue
        lines[(*path, len(items))] = sc.line()
        items.append(_expr(sc, (*path, len(items)), lines))


def _object(sc: _Scanner, path: Path, lines: dict) -> dict:
    sc.i += 1
    out: dict = {}
    while True:
        sc.skip()
        if sc.eof():
            return out
        if sc.peek() == "}":
            sc.i += 1
            return out
        if sc.peek() == ",":
            sc.i += 1
            continue
        start = sc.i
        key = _plain(sc.string()) if sc.peek() == '"' else sc.ident()
        sc.skip(newlines=False)
        if not key or sc.peek() not in ("=", ":"):
            sc.raw_until_end()
            if sc.peek() not in ("}",):
                sc.i += 1 if not sc.eof() else 0
            continue
        sc.i += 1
        lines[(*path, key)] = sc.line(start)
        out[key] = _expr(sc, (*path, key), lines)


def has_unresolved(value: Any) -> bool:
    if isinstance(value, Unresolved):
        return True
    if isinstance(value, dict):
        return any(has_unresolved(v) for v in value.values())
    if isinstance(value, list):
        return any(has_unresolved(v) for v in value)
    return False
