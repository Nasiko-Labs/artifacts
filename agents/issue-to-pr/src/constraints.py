"""Pure: the user's constraints read from their own words (never from the issue body): diff only, no test
changes, only under a directory, no new files, and whether they said "fixes"/"closes" (which decides
`Closes #n` over `Refs #n`)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_DIFF_ONLY = re.compile(r"\b(?:just |only )?show me the diff(?: only)?\b|\bdiff only\b|\bjust the diff\b|"
                        r"\bdon'?t open (?:a |the )?(?:pr|pull request)\b|\bno (?:pr|pull request)\b", re.IGNORECASE)
_NO_TESTS = re.compile(r"\b(?:don'?t|do not|never) (?:change|touch|edit|modify) (?:the )?tests?\b|"
                       r"\bno test changes\b", re.IGNORECASE)
_ONLY_IN = re.compile(r"\bonly (?:in|under|inside)\s+`?([\w./-]+?)`?(?=[\s,.;]|$)", re.IGNORECASE)
_NO_NEW = re.compile(r"\bno new files?\b|\b(?:don'?t|do not) (?:add|create) (?:any )?(?:new )?files?\b", re.IGNORECASE)
_CLOSES = re.compile(r"\b(?:fixes|closes|resolves)\b", re.IGNORECASE)
_AVOID = re.compile(r"\b(?:don'?t|do not) (?:touch|change|edit)\s+`([^`]+)`", re.IGNORECASE)


@dataclass(frozen=True)
class Constraints:
    diff_only: bool = False
    no_test_changes: bool = False
    only_in: tuple[str, ...] = ()
    no_new_files: bool = False
    closes: bool = False
    avoid: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Any) -> Constraints:
        if not isinstance(data, dict):
            return Constraints()
        return Constraints(bool(data.get("diff_only")), bool(data.get("no_test_changes")),
                           tuple(data.get("only_in") or ()), bool(data.get("no_new_files")),
                           bool(data.get("closes")), tuple(data.get("avoid") or ()))

    def words(self) -> list[str]:
        """The constraints as plain rules for the engine's task text."""
        out = []
        if self.no_test_changes:
            out.append("Do not change any test file.")
        if self.only_in:
            out.append(f"Change only files under: {', '.join(self.only_in)}.")
        if self.no_new_files:
            out.append("Do not create new files.")
        out += [f"Do not touch {a}." for a in self.avoid]
        return out


def read(user_words: str) -> Constraints:
    text = user_words or ""
    only = tuple(dict.fromkeys(m.rstrip("/.") for m in _ONLY_IN.findall(text)))
    return Constraints(bool(_DIFF_ONLY.search(text)), bool(_NO_TESTS.search(text)), only,
                       bool(_NO_NEW.search(text)), bool(_CLOSES.search(text)),
                       tuple(dict.fromkeys(_AVOID.findall(text))))


def merge(base: Constraints, extra: Constraints) -> Constraints:
    return Constraints(base.diff_only or extra.diff_only, base.no_test_changes or extra.no_test_changes,
                       tuple(dict.fromkeys((*base.only_in, *extra.only_in))), base.no_new_files or extra.no_new_files,
                       base.closes or extra.closes, tuple(dict.fromkeys((*base.avoid, *extra.avoid))))
