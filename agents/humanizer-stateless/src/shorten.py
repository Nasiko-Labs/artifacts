"""Pure: shorten a text in code when the model could not get it under the word cap ("make it shorter" never gives
up). Only filler goes, in this order, stopping as soon as the text fits:

    1. leading connectives ("Moreover,", "In short,") and fact-free introductory clauses ("In today's market,")
    2. whole sentences that carry no fact (number, link or name), the last one first
    3. filler words ("very", "really", "just")

Every fact stays, and every line keeps at least one sentence, so paragraphs, list items and headings keep their
shape. The result may still be over the cap when only facts are left; it is never longer than the input."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

import grounding as g
from patterns import word_count

MIN_CLAUSE_REST_WORDS = 3
_CONNECTIVE = re.compile(
    r"^(?:moreover|additionally|furthermore|in addition|also|basically|essentially|ultimately|overall|indeed|"
    r"notably|importantly|in short|in summary|in conclusion|of course|in fact|that said|as such|honestly)\s*,\s*",
    re.IGNORECASE)
_INTRO = re.compile(r"^([^,]{3,80}),\s+(?=\S)")
_FILLER = re.compile(r"\b(?:very|really|just|actually|quite|simply|truly|completely|totally|extremely|"
                     r"certainly|definitely)\s+", re.IGNORECASE)
_PREFIX = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+)?")
_HEADING = re.compile(r"^\s*#{1,6}\s")

Lines = tuple["_Line", ...]


@dataclass(frozen=True)
class _Line:
    """One line: its list marker (kept as is) and its sentences; headings and blank lines are left alone."""

    raw: str
    prefix: str = ""
    sentences: tuple[str, ...] = ()
    fixed: bool = True

    def render(self) -> str:
        return self.raw if self.fixed else self.prefix + " ".join(self.sentences)


def cut_to(text: str, cap: int) -> str:
    names = g.names(text)
    lines = tuple(_parse(line) for line in (text or "").split("\n"))
    for step in (_strip_openers, _drop_sentences, _drop_fillers):
        if _words(lines) <= cap:
            break
        lines = step(lines, cap, names)
    return "\n".join(line.render() for line in lines)


def has_fact(sentence: str, names: set[str]) -> bool:
    return bool(g.numbers(sentence) or g.links(sentence) or any(g.has_word(n, sentence) for n in names))


def _parse(raw: str) -> _Line:
    if not raw.strip() or _HEADING.match(raw):
        return _Line(raw)
    prefix = _PREFIX.match(raw).group(0)
    return _Line(raw, prefix, tuple(g.sentences(raw[len(prefix):])), False)


def _words(lines: Lines) -> int:
    return sum(word_count(line.render()) for line in lines)


def _with_sentence(lines: Lines, at: int, index: int, sentence: str | None) -> Lines:
    """`lines` with one sentence replaced (or removed when `sentence` is None)."""
    old = lines[at].sentences
    new = old[:index] + (() if sentence is None else (sentence,)) + old[index + 1:]
    return lines[:at] + (replace(lines[at], sentences=new),) + lines[at + 1:]


def _without_opener(sentence: str, names: set[str]) -> str | None:
    connective = _CONNECTIVE.match(sentence)
    if connective:
        return _capitalise(sentence[connective.end():])
    intro = _INTRO.match(sentence)
    if intro and not has_fact(intro.group(1), names):
        rest = sentence[intro.end():]
        return _capitalise(rest) if word_count(rest) >= MIN_CLAUSE_REST_WORDS else None
    return None


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:]


def _positions(lines: Lines) -> list[tuple[int, int]]:
    return [(at, i) for at, line in enumerate(lines) for i in range(len(line.sentences))]


def _strip_openers(lines: Lines, cap: int, names: set[str]) -> Lines:
    changed = True
    while changed and _words(lines) > cap:
        changed = False
        for at, i in _positions(lines):
            shorter = _without_opener(lines[at].sentences[i], names)
            if shorter is not None and _words(lines) > cap:
                lines, changed = _with_sentence(lines, at, i, shorter), True
    return lines


def _drop_sentences(lines: Lines, cap: int, names: set[str]) -> Lines:
    for at, i in reversed(_positions(lines)):
        if _words(lines) <= cap:
            break
        if len(lines[at].sentences) > 1 and not has_fact(lines[at].sentences[i], names):
            lines = _with_sentence(lines, at, i, None)
    return lines


def _drop_fillers(lines: Lines, cap: int, names: set[str]) -> Lines:
    return _each_sentence(lines, cap, lambda s: _FILLER.sub("", s))


def _each_sentence(lines: Lines, cap: int, edit: Callable[[str], str]) -> Lines:
    for at, i in _positions(lines):
        if _words(lines) <= cap:
            break
        lines = _with_sentence(lines, at, i, edit(lines[at].sentences[i]))
    return lines
