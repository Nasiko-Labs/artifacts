"""Pure: reads the lines of one resource block in the human plan text: attributes with their nested path
(`block.attr`), list values under the list's name, heredocs, and the before/after text the pattern rules scan."""

from __future__ import annotations

import re

from changes import SENSITIVE, UNKNOWN, Attr

_ATTR = re.compile(r'^\s*(?P<sym>[-+~](?=\s))?\s*(?P<name>"[^"]+"|[\w\-./:]+)\s*=\s*(?P<val>.*?)\s*$')
_OPEN = re.compile(r'^\s*(?P<sym>[-+~](?=\s))?\s*(?P<name>"[^"]+"|[\w\-./:]+)?\s*\{\s*$')
_CLOSE = re.compile(r"^\s*[\]\)}][\]\)},]*\s*(?:->\s*\S.*)?$")
_HEREDOC = re.compile(r"^<<-?(\w+)$")
_FORCES = re.compile(r"\s*# forces replacement\s*$")
_SYM = re.compile(r"^\s*([-+~])\s")


class BlockReader:
    """Reads one resource block's lines: attributes with their nested path, and the before/after text."""

    def __init__(self) -> None:
        self.attrs: list[Attr] = []
        self.before: list[str] = []
        self.after: list[str] = []
        self.path: list[str] = []
        self.depth = 0
        self.heredoc: tuple[str, str, str, list[str], str] | None = None

    def feed(self, raw: str) -> None:
        if self.heredoc is not None:
            self._heredoc_line(raw)
            return
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            return
        sym_match = _SYM.match(raw)
        sym = sym_match.group(1) if sym_match else ""
        self._texts(sym, stripped)
        if _CLOSE.match(raw):
            self.depth -= 1
            if self.path:
                self.path.pop()
            return
        opened = _OPEN.match(raw)
        if opened:
            self._push(opened.group("name") or "")
            return
        attr = _ATTR.match(raw)
        if attr:
            self._attr(attr.group("sym") or "", _unquote(attr.group("name")), attr.group("val"), stripped)
        elif self.path:  # a list value: kept under the list's name
            body = stripped[1:].strip() if sym else stripped
            self.attrs.append(Attr(self._name(""), *_sides(sym, body), line=stripped))

    def _attr(self, sym: str, name: str, value: str, line: str) -> None:
        forces = bool(_FORCES.search(value))
        value = _FORCES.sub("", value).rstrip(",").strip()
        heredoc = _HEREDOC.match(value.split(" -> ")[0].strip())
        if heredoc:
            self.heredoc = (sym, name, heredoc.group(1), [], line)
            return
        if value.endswith(("{", "[", "(")):
            self._push(name)
            return
        before, after = _sides(sym, value)
        self.attrs.append(Attr(self._name(name), before, after, forces, line))

    def _heredoc_line(self, raw: str) -> None:
        sym, name, tag, collected, line = self.heredoc
        if raw.strip() == tag:
            text = "\n".join(collected)
            self.attrs.append(Attr(self._name(name), *_sides(sym, text, split=False), line=line))
            self.heredoc = None
            return
        collected.append(raw.strip())
        target = self.before if sym == "-" else self.after
        target.append(raw.strip())

    def _push(self, name: str) -> None:
        self.depth += 1
        self.path.append(_unquote(name))

    def _name(self, name: str) -> str:
        return ".".join(p for p in (*self.path, name) if p)

    def _texts(self, sym: str, stripped: str) -> None:
        body = stripped[1:].strip() if sym else stripped
        if sym == "~" and " -> " in body:
            left, right = body.split(" -> ", 1)
            self.before.append(left)
            self.after.append(right)
        elif sym == "-":
            self.before.append(body)
        elif sym == "+":
            self.after.append(body)
        else:
            self.before.append(body)
            self.after.append(body)


def _sides(sym: str, value: str, split: bool = True) -> tuple[str | None, str | None]:
    if split and " -> " in value:
        left, right = value.split(" -> ", 1)
        return _value(left), _value(right)
    if sym == "+":
        return None, _value(value)
    if sym == "-":
        return _value(value), None
    return _value(value), _value(value)


def _value(text: str) -> str | None:
    value = text.strip().rstrip(",").strip()
    if value in ("null", ""):
        return None
    if value == "(known after apply)":
        return UNKNOWN
    if value.startswith("(sensitive"):
        return SENSITIVE
    return _unquote(value)


def _unquote(text: str) -> str:
    text = text.strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] == '"' else text
