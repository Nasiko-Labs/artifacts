"""Pure: a regular-expression tokeniser. Each token keeps its source span, nesting depth and a plain-English
description, so the explanation can quote the pattern exactly. `/…/flags` literals are split here."""

from __future__ import annotations

import re
from dataclasses import dataclass

FLAG_WORDS = {"i": "case-insensitive", "m": "multiline: `^` and `$` match at every line",
              "s": "dot matches newline too", "g": "global: find every match (JavaScript; no effect on match or "
              "no-match)", "u": "unicode mode", "y": "sticky: match only at lastIndex (JavaScript)",
              "x": "verbose: whitespace and # comments ignored", "d": "indices of groups (JavaScript)"}
ESCAPES = {"d": "a digit", "D": "a non-digit", "w": "a word character (letter, digit or _)",
           "W": "a non-word character", "s": "a whitespace character", "S": "a non-whitespace character",
           "b": "a word boundary", "B": "not a word boundary", "A": "the start of the string",
           "Z": "the end of the string", "z": "the very end of the string", "n": "a newline", "t": "a tab",
           "r": "a carriage return", "G": "where the last match ended"}
_LITERAL = re.compile(r"^/(.+)/([a-z]*)$", re.DOTALL)
_BRACES = re.compile(r"\{(\d+)(,(\d*))?\}")
_GROUP_KINDS = (("(?:", "non-capturing group"), ("(?=", "lookahead (followed by)"),
                ("(?!", "negative lookahead (not followed by)"), ("(?<=", "lookbehind (preceded by)"),
                ("(?<!", "negative lookbehind (not preceded by)"), ("(?>", "atomic group"))
_NAMED = re.compile(r"\(\?P?<([A-Za-z_]\w*)>")
_INLINE_FLAGS = re.compile(r"\(\?([aiLmsux-]+)(:|\))")


@dataclass(frozen=True)
class Token:
    kind: str       # literal escape class group close quant anchor alt dot backref
    text: str
    start: int
    depth: int
    desc: str


def split_literal(text: str) -> tuple[str, str]:
    """`/abc/gi` → ("abc", "gi"); anything else → (text, "")."""
    match = _LITERAL.match(text.strip())
    if match and set(match.group(2)) <= set(FLAG_WORDS):
        return match.group(1), match.group(2)
    return text, ""


def tokenize(pattern: str) -> list[Token]:
    tokens: list[Token] = []
    i, depth = 0, 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            text = _escape_text(pattern, i)
            tokens.append(Token(*_escape(text), i, depth))
        elif char == "[":
            text = _class_text(pattern, i)
            tokens.append(Token("class", text, i, depth, _class_desc(text)))
        elif char == "(":
            text, desc = _group(pattern, i)
            tokens.append(Token("group", text, i, depth, desc))
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
            text = ")"
            tokens.append(Token("close", text, i, depth, "end of group"))
        elif char in "*+?{":
            text = _quant_text(pattern, i)
            tokens.append(Token("quant" if text else "literal", text or char, i, depth,
                                _quant_desc(text) if text else f"the character `{char}`"))
            text = text or char
        elif char == "|":
            text = char
            tokens.append(Token("alt", text, i, depth, "or"))
        elif char in "^$":
            text = char
            tokens.append(Token("anchor", text, i, depth, "start of the string (or line with m)" if char == "^"
                                else "end of the string (or line with m)"))
        elif char == ".":
            text = char
            tokens.append(Token("dot", text, i, depth, "any character except newline"))
        else:
            text = char
            tokens.append(Token("literal", text, i, depth, f"the character `{char}`"))
        i += len(text)
    return tokens


def _escape_text(pattern: str, i: int) -> str:
    rest = pattern[i:]
    for regex in (r"\\[pP]\{[^}]*\}", r"\\k<[^>]*>", r"\\x[0-9A-Fa-f]{2}", r"\\u[0-9A-Fa-f]{4}", r"\\\d+", r"\\.?"):
        match = re.match(regex, rest, re.DOTALL)
        if match:
            return match.group(0)
    return "\\"


def _escape(text: str) -> tuple[str, str, str]:
    body = text[1:]
    if body[:1].isdigit() or body.startswith("k<"):
        return "backref", text, f"the same text as group {body.strip('k<>')} matched"
    if body[:1] in "pP":
        return "escape", text, f"a character {'not ' if body[0] == 'P' else ''}in Unicode class {body[2:-1]}"
    if body in ESCAPES:
        kind = "anchor" if body in "bBAZzG" else "escape"
        return kind, text, ESCAPES[body]
    return "escape", text, f"the character `{body}`" if body else "a trailing backslash"


def _class_text(pattern: str, i: int) -> str:
    j = i + 1
    if j < len(pattern) and pattern[j] == "^":
        j += 1
    if j < len(pattern) and pattern[j] == "]":
        j += 1
    while j < len(pattern) and pattern[j] != "]":
        j += 2 if pattern[j] == "\\" else 1
    return pattern[i:j + 1]


def _class_desc(text: str) -> str:
    negated = text.startswith("[^")
    return f"one character {'not ' if negated else ''}in the set `{text}`"


def _group(pattern: str, i: int) -> tuple[str, str]:
    rest = pattern[i:]
    named = _NAMED.match(rest)
    if named:
        return named.group(0), f"capturing group named `{named.group(1)}`"
    for prefix, desc in _GROUP_KINDS:
        if rest.startswith(prefix):
            return prefix, desc
    flags = _INLINE_FLAGS.match(rest)
    if flags:
        return flags.group(0)[:-1] if flags.group(2) == ":" else flags.group(0), f"inline flags `{flags.group(1)}`"
    return "(", "capturing group"


def _quant_text(pattern: str, i: int) -> str:
    char = pattern[i]
    if char == "{":
        match = _BRACES.match(pattern, i)
        if not match:
            return ""
        text = match.group(0)
    else:
        text = char
    nxt = pattern[i + len(text):i + len(text) + 1]
    return text + nxt if nxt in ("?", "+") else text


def _quant_desc(text: str) -> str:
    mode = " (lazy: as few as possible)" if text.endswith("?") and len(text) > 1 else (
        " (possessive: never gives back)" if text.endswith("+") and len(text) > 1 else "")
    core = text[:-1] if mode else text
    words = {"*": "zero or more times", "+": "one or more times", "?": "optional (zero or one time)"}
    if core in words:
        return words[core] + mode
    match = _BRACES.match(core)
    low, comma, high = match.group(1), match.group(2), match.group(3)
    if not comma:
        return f"exactly {low} times" + mode
    return (f"{low} to {high} times" if high else f"{low} or more times") + mode


def capture_count(tokens: list[Token]) -> int:
    return sum(1 for t in tokens if t.kind == "group" and t.desc.startswith("capturing"))


def quantifier_after(tokens: list[Token], index: int) -> Token | None:
    nxt = tokens[index + 1] if index + 1 < len(tokens) else None
    return nxt if nxt is not None and nxt.kind == "quant" else None


def unbounded(quant: Token | None) -> bool:
    if quant is None:
        return False
    text = quant.text
    core = text[:-1] if len(text) > 1 and text[-1] in "?+" else text
    return core in ("*", "+") or core.endswith(",}")
