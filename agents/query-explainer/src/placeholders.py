"""Pure: templated SQL made parseable and back again.

Jinja expressions (`{{ ref('orders') }}`) become identifiers `__jinja_n__`; Jinja control and comment tags
(`{% if %}`, `{# #}`) are removed for parsing; bind parameters (`:name`, `%s`, `%(name)s`, `$1`, `?`, `@p`)
become `__param_n__`. Text inside string literals and comments is never touched. `unmask` restores every
original in any output string."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SKIP = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", re.DOTALL)
_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_JINJA_TAG = re.compile(r"\{%.*?%\}|\{#.*?#\}", re.DOTALL)
_PARAM = re.compile(r"(?<![\w:]):[A-Za-z_]\w*|%\(\w+\)s|%s|\$\d+|\?|(?<![\w@])@[A-Za-z_]\w*")
_TOKEN = re.compile(r"__(?:jinja|param)_\d+__")


@dataclass(frozen=True)
class Masked:
    sql: str
    originals: dict[str, str] = field(default_factory=dict)
    removed_tags: int = 0

    @property
    def has_placeholders(self) -> bool:
        return bool(self.originals) or self.removed_tags > 0


def mask(sql: str) -> Masked:
    originals: dict[str, str] = {}

    def keep(prefix: str):
        def sub(match: re.Match) -> str:
            token = f"__{prefix}_{sum(1 for k in originals if k.startswith(f'__{prefix}_')) + 1}__"
            originals[token] = match.group(0)
            return token
        return sub

    text = _JINJA_EXPR.sub(keep("jinja"), sql or "")
    tags = len(_JINJA_TAG.findall(text))
    text = _JINJA_TAG.sub(" ", text)
    text = _outside_literals(text, lambda chunk: _PARAM.sub(keep("param"), chunk))
    return Masked(text, originals, tags)


def unmask(text: str, originals: dict[str, str]) -> str:
    if not originals or not text:
        return text
    return _TOKEN.sub(lambda m: originals.get(m.group(0), m.group(0)), text)


def unmask_deep(value, originals: dict[str, str]):
    """`unmask` over every string inside a JSON-like value; returns a new value."""
    if isinstance(value, str):
        return unmask(value, originals)
    if isinstance(value, list):
        return [unmask_deep(v, originals) for v in value]
    if isinstance(value, dict):
        return {k: unmask_deep(v, originals) for k, v in value.items()}
    return value


def _outside_literals(text: str, fn) -> str:
    out: list[str] = []
    pos = 0
    for match in _SKIP.finditer(text):
        out.append(fn(text[pos:match.start()]))
        out.append(match.group(0))
        pos = match.end()
    out.append(fn(text[pos:]))
    return "".join(out)
