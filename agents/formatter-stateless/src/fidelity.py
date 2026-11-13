"""Pure: the fidelity check. Every scalar string and number in the input (except the footer keys) must
appear in the rendered output once the target's escaping is undone. Values a renderer left out on
purpose (cut cells, collapsed Slack blocks) are excluded and counted. Markdown fields are converted, not
copied, so for them every word must appear, in order.

A failure is a bug in a renderer: the caller falls back to Markdown, never to partial output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import md_blocks
from shapes import FOOTER_KEYS, MARKDOWN_KEYS

_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class Result:
    missing: tuple[str, ...]
    excluded: int

    @property
    def ok(self) -> bool:
        return not self.missing


def expected(values: list[Any]) -> tuple[list[str], list[str]]:
    """(plain values, markdown values) that must survive rendering."""
    plain: list[str] = []
    markdown: list[str] = []
    for value in values:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in FOOTER_KEYS and not isinstance(item, (dict, list)):
                    continue
                if isinstance(item, str) and (key.endswith("_markdown") or key in MARKDOWN_KEYS):
                    markdown.append(item)
                else:
                    _walk(item, plain)
        else:
            _walk(value, plain)
    return plain, markdown


def _walk(value: Any, out: list[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _walk(item, out)
    elif isinstance(value, list):
        for item in value:
            _walk(item, out)
    elif isinstance(value, str):
        out.append(value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out.append(json.dumps(value))


def _norm(text: str) -> str:
    return " ".join(text.split())


def _json_escaped(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)[1:-1]


def check(values: list[Any], visible: str, excluded: list[str]) -> Result:
    """`visible` is the output with the target's escaping undone."""
    plain, markdown = expected(values)
    out = _norm(visible)
    left_out = [_norm(text) for text in excluded]
    missing: list[str] = []
    skipped = 0
    for value in plain:
        lines = [_norm(line) for line in value.split("\n") if line.strip()]
        absent = [line for line in lines if not _found(line, [out])]
        if not absent:
            continue
        if all(_found(line, left_out) for line in absent):
            skipped += 1
        else:
            missing.append(value)
    words = _WORD.findall(visible)
    missing += [value for value in markdown if not _in_order(_WORD.findall(md_blocks.plain_words_source(value)), words)]
    return Result(tuple(missing), skipped)


def words_kept(markdown: str, visible: str) -> bool:
    """Every word a reader sees in `markdown` appears in `visible`, in order."""
    return _in_order(_WORD.findall(md_blocks.plain_words_source(markdown)), _WORD.findall(visible))


def _found(line: str, haystacks: list[str]) -> bool:
    escaped = _norm(_json_escaped(line))
    return any(line in hay or escaped in hay for hay in haystacks)


def _in_order(needles: list[str], words: list[str]) -> bool:
    it = iter(words)
    return all(any(word == needle for word in it) for needle in needles)
