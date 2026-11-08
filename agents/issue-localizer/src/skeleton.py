"""Pure: a file's skeleton (declarations with line spans), by language. Line numbers are computed here and
nowhere else; the model only ever sees `name (kind, signature)`."""

from __future__ import annotations

import re
from dataclasses import dataclass

import skeleton_braces as braces
import skeleton_py
from skeleton_types import Symbol

MAX_DECLARATIONS_SHOWN = 120
WHOLE_FILE = "<file>"

_BRACE_LANGS = {
    ".js": braces.JS, ".jsx": braces.JS, ".ts": braces.JS, ".tsx": braces.JS, ".mjs": braces.JS, ".cjs": braces.JS,
    ".go": braces.GO, ".java": braces.JVM, ".kt": braces.JVM, ".cs": braces.JVM, ".rs": braces.RUST,
}
_RUBY = re.compile(r"^(\s*)(def|class|module)\s+([\w.:?!]+)")
_PY_DEF = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+(\w+)")


@dataclass(frozen=True)
class Skeleton:
    symbols: tuple[Symbol, ...]
    supported: bool

    def names(self) -> frozenset[str]:
        return frozenset(s.name for s in self.symbols)

    def get(self, name: str) -> Symbol | None:
        return next((s for s in self.symbols if s.name == name), None)

    def for_model(self) -> str:
        shown = self.symbols[:MAX_DECLARATIONS_SHOWN]
        return "\n".join(f"{s.name} ({s.kind}, {s.signature})" for s in shown)


def build(path: str, text: str) -> Skeleton:
    suffix = "." + path.rsplit(".", 1)[-1].casefold() if "." in path.rsplit("/", 1)[-1] else ""
    line_count = max(1, len(text.split("\n")))
    symbols: tuple[Symbol, ...] | None = None
    if suffix == ".py":
        symbols = skeleton_py.build(text)
        if symbols is None:
            symbols = _indented(text, _PY_DEF)
    elif suffix in _BRACE_LANGS:
        symbols = braces.build(text, _BRACE_LANGS[suffix])
    elif suffix == ".rb":
        symbols = _ruby(text)
    if not symbols:
        whole = (Symbol(WHOLE_FILE, "file", 1, line_count, path),)
        return Skeleton(whole, symbols is not None)
    return Skeleton(symbols, True)


def _indented(text: str, pattern: re.Pattern) -> tuple[Symbol, ...]:
    """Fallback for Python that does not parse: a block ends before the next line at its indent or less."""
    lines = text.split("\n")
    found = []
    for i, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        end = next((j for j in range(i + 1, len(lines))
                    if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= indent), len(lines))
        kind = "class" if match.group(2) == "class" else ("method" if indent else "function")
        found.append(Symbol(match.group(3), kind, i + 1, _trim(lines, i, end), line.strip()[:160]))
    return tuple(found)


def _ruby(text: str) -> tuple[Symbol, ...]:
    """`def`/`class`/`module` closed by the first `end` at the same indentation."""
    lines = text.split("\n")
    found = []
    for i, line in enumerate(lines):
        match = _RUBY.match(line)
        if not match:
            continue
        indent = match.group(1)
        end = next((j for j in range(i + 1, len(lines)) if lines[j].rstrip() == f"{indent}end"), len(lines) - 1)
        kind = {"def": "method", "class": "class", "module": "module"}[match.group(2)]
        found.append(Symbol(match.group(3), kind, i + 1, end + 1, line.strip()[:160]))
    return tuple(found)


def _trim(lines: list[str], start: int, stop: int) -> int:
    """The last non-blank line before `stop`, as a 1-based number."""
    end = stop - 1
    while end > start and not lines[end].strip():
        end -= 1
    return end + 1
