"""Pure: PR metadata (title, author, branches, description) read from tool output by shape.

A pull request object is any JSON object with a `title` plus `head` and `base` objects (the
GitHub REST shape, however a connector wraps it). When no tool output has that shape, the
fetch step's own JSON answer fills in what it can.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from diff import json_values

MAX_DESCRIPTION_CHARS = 6_000


@dataclass(frozen=True)
class PrInfo:
    title: str = ""
    author: str = ""
    description: str = ""
    base: str = ""
    head: str = ""
    state: str = ""

    def is_empty(self) -> bool:
        return not (self.title or self.description or self.base or self.head)

    def merged_with(self, fallback: PrInfo) -> PrInfo:
        """Fields from this (tool-derived) info, gaps filled from `fallback` (the model's answer)."""
        mine, theirs = asdict(self), asdict(fallback)
        return PrInfo(**{k: mine[k] or theirs[k] for k in mine})

    def to_state(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_mapping(raw: Mapping[str, Any] | None) -> PrInfo:
        raw = raw or {}
        return PrInfo(**{k: _text(raw.get(k))[:MAX_DESCRIPTION_CHARS] for k in PrInfo.__dataclass_fields__})


def extract_pr_info(tool_texts: Iterable[str]) -> PrInfo:
    for text in tool_texts:
        for value in json_values(text):
            for raw in _pull_objects(value):
                return _info(raw)
    return PrInfo()


def _pull_objects(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _pull_objects(item)
    elif isinstance(value, Mapping):
        if isinstance(value.get("title"), str) and isinstance(value.get("head"), Mapping) \
                and isinstance(value.get("base"), Mapping):
            yield value
            return
        for item in value.values():
            yield from _pull_objects(item)


def _info(raw: Mapping[str, Any]) -> PrInfo:
    user = raw.get("user") if isinstance(raw.get("user"), Mapping) else {}
    state = _text(raw.get("state"))
    if raw.get("merged") is True:
        state = "merged"
    elif raw.get("draft") is True and state == "open":
        state = "draft"
    return PrInfo(
        title=_text(raw.get("title")),
        author=_text(user.get("login")),
        description=_text(raw.get("body"))[:MAX_DESCRIPTION_CHARS],
        base=_text(raw["base"].get("ref")),
        head=_text(raw["head"].get("ref")),
        state=state,
    )


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
