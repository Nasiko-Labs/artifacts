"""Pure: reading the JSON results a user pastes for a merge. Accepts fenced json blocks, a JSON array
of results, or objects one after another (separated by --- or nothing). Anything that does not parse
is named by position, never guessed at."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCE = re.compile(r"```[ \t]*(?:json)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_START = re.compile(r"[\[{]")
_NEXT_LINE_START = re.compile(r"\n[ \t]*[\[{]")
_DECODER = json.JSONDecoder()


@dataclass(frozen=True)
class Pasted:
    values: tuple
    problems: tuple[str, ...] = ()


def extract_values(text: str) -> Pasted:
    blocks = _FENCE.findall(text or "")
    if blocks:
        values, problems = [], []
        for n, block in enumerate(blocks, 1):
            try:
                values += _flatten(json.loads(block))
            except ValueError as e:
                problems.append(f"Block {n} isn't valid JSON ({e.msg} at line {e.lineno}); ignored.")
        return Pasted(tuple(values), tuple(problems))
    return _scan(text or "")


def _scan(text: str) -> Pasted:
    values, problems, pos = [], [], 0
    while True:
        match = _JSON_START.search(text, pos)
        if match is None:
            break
        try:
            value, end = _DECODER.raw_decode(text, match.start())
        except ValueError as e:
            problems.append(f"The JSON starting at character {match.start() + 1:,} doesn't parse ({e.msg}); "
                            "ignored.")
            nxt = _NEXT_LINE_START.search(text, match.start() + 1)
            if nxt is None:
                break
            pos = nxt.start() + 1
            continue
        values += _flatten(value)
        pos = end
    return Pasted(tuple(values), tuple(problems))


def _flatten(value) -> list:
    """A JSON array of results counts as its elements; anything else is one result."""
    return list(value) if isinstance(value, list) else [value]


def fenced(value) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"
