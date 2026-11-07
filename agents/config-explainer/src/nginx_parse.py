"""Pure: an nginx configuration parser. Tokens (words, quoted strings, `{`, `}`, `;`, `#` comments) build a
tree of directives with line numbers. `include` is kept as an unresolved node. A syntax error stops the
parse and is reported with its line, keeping the tree read so far."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Directive:
    name: str
    args: tuple[str, ...]
    line: int
    block: tuple[Directive, ...] | None = None

    def children(self, name: str) -> list[Directive]:
        return [d for d in self.block or () if d.name == name]

    def first(self, name: str) -> Directive | None:
        found = self.children(name)
        return found[0] if found else None


@dataclass(frozen=True)
class ParseResult:
    tree: tuple[Directive, ...]
    error: str | None = None
    includes: tuple[str, ...] = field(default_factory=tuple)


def _tokens(text: str) -> Iterator[tuple[str, int, bool]]:
    """(token, line, quoted)."""
    i, line = 0, 1
    while i < len(text):
        char = text[i]
        if char == "\n":
            line += 1
            i += 1
        elif char.isspace():
            i += 1
        elif char == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
        elif char in "{};":
            yield char, line, False
            i += 1
        elif char in "\"'":
            end = i + 1
            while end < len(text) and text[end] != char:
                end += 2 if text[end] == "\\" else 1
            yield text[i + 1:end], line, True
            line += text[i:end].count("\n")
            i = end + 1
        else:
            start = i
            while i < len(text) and not text[i].isspace() and text[i] not in "{};":
                if text[i] == "$" and text[i + 1:i + 2] == "{":
                    i = text.find("}", i) + 1 or len(text)
                    continue
                i += 1
            yield text[start:i], line, False


class _Error(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.line = line


def parse(text: str) -> ParseResult:
    tokens = list(_tokens(text or ""))
    stack: list[tuple[str, tuple[str, ...], int, list[Directive]]] = [("", (), 0, [])]
    words: list[tuple[str, int, bool]] = []
    try:
        for token, line, quoted in tokens:
            if quoted or token not in "{};":
                words.append((token, line, quoted))
            elif token == ";":
                if not words:
                    continue
                stack[-1][3].append(Directive(words[0][0], tuple(w for w, _, _ in words[1:]), words[0][1]))
                words = []
            elif token == "{":
                if not words:
                    raise _Error("a `{` with no directive before it", line)
                stack.append((words[0][0], tuple(w for w, _, _ in words[1:]), words[0][1], []))
                words = []
            else:
                if words:
                    raise _Error(f"missing `;` after `{' '.join(w for w, _, _ in words)}`", words[-1][1])
                if len(stack) == 1:
                    raise _Error("an unexpected `}`", line)
                name, args, start, body = stack.pop()
                stack[-1][3].append(Directive(name, args, start, tuple(body)))
        if words:
            raise _Error(f"missing `;` after `{' '.join(w for w, _, _ in words)}`", words[-1][1])
        if len(stack) > 1:
            raise _Error(f"the `{stack[-1][0]}` block opened on line {stack[-1][2]} is never closed", stack[-1][2])
    except _Error as exc:
        tree = _unwind(stack)
        return ParseResult(tree, f"line {exc.line}: {exc}", tuple(_includes(tree)))
    tree = tuple(stack[0][3])
    return ParseResult(tree, None, tuple(_includes(tree)))


def _unwind(stack: list) -> tuple[Directive, ...]:
    while len(stack) > 1:
        name, args, start, body = stack.pop()
        stack[-1][3].append(Directive(name, args, start, tuple(body)))
    return tuple(stack[0][3])


def walk(tree: tuple[Directive, ...] | None) -> Iterator[Directive]:
    for directive in tree or ():
        yield directive
        yield from walk(directive.block)


def _includes(tree: tuple[Directive, ...]) -> list[str]:
    return [" ".join(d.args) for d in walk(tree) if d.name == "include"]


def servers(tree: tuple[Directive, ...]) -> list[Directive]:
    """`server` blocks under `http` or at the top level (a pasted server-only snippet)."""
    return [d for d in walk(tree) if d.name == "server" and d.block is not None]


def locations(block: Directive) -> list[Directive]:
    return [d for d in block.block or () if d.name == "location" and d.block is not None]


def location_key(location: Directive) -> tuple[str, str]:
    """(modifier, path): modifier is one of "", "=", "^~", "~", "~*", "@"."""
    args = location.args
    if len(args) >= 2 and args[0] in ("=", "^~", "~", "~*"):
        return args[0], args[1]
    if args and args[0].startswith("@"):
        return "@", args[0]
    for modifier in ("^~", "~*", "=", "~"):
        if args and args[0].startswith(modifier) and len(args[0]) > len(modifier):
            return modifier, args[0][len(modifier):]
    return "", args[0] if args else ""


def all_locations(block: Directive) -> list[Directive]:
    found = []
    for location in locations(block):
        found.append(location)
        found += all_locations(location)
    return found
