"""Pure: a safe YAML loader that remembers where every mapping and key sits.

Each mapping comes back as `LDict` (a dict) with `.line` (its first line) and `.key_lines` (key -> line),
1-based and relative to the text given. CloudFormation short tags (`!Ref`, `!Sub`, `!GetAtt`, ...) load as
plain values instead of failing. JSON is valid YAML here, so JSON monitors get line numbers too.
"""

from __future__ import annotations

import json
from typing import Any

import yaml


class LDict(dict):
    line: int = 0
    key_lines: dict[str, int]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key_lines = {}

    def line_of(self, key: str) -> int:
        return self.key_lines.get(key) or self.line


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader: _Loader, node: yaml.MappingNode) -> LDict:
    loader.flatten_mapping(node)
    result = LDict()
    result.line = node.start_mark.line + 1
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            key = str(key)
        result[key] = loader.construct_object(value_node, deep=True)
        result.key_lines[key] = key_node.start_mark.line + 1
    return result


def _tagged(loader: _Loader, tag_suffix: str, node: yaml.Node) -> Any:
    """`!Ref Foo` -> "!Ref Foo"; a tagged list or mapping keeps its value."""
    if isinstance(node, yaml.ScalarNode):
        return f"!{tag_suffix} {loader.construct_scalar(node)}".strip()
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return _mapping(loader, node)


def _plain_timestamp(loader: _Loader, node: yaml.Node) -> str:
    return loader.construct_scalar(node)  # keep dates as written (never re-format a value)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
_Loader.add_constructor("tag:yaml.org,2002:timestamp", _plain_timestamp)
_Loader.add_multi_constructor("!", _tagged)


def load(text: str, first_line: int = 1) -> Any:
    """One YAML (or JSON) document; line numbers count from `first_line`. Raises ValueError."""
    padded = "\n" * max(0, first_line - 1) + text
    try:
        return yaml.load(padded, Loader=_Loader)  # _Loader derives from SafeLoader
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        raise ValueError(f"not valid YAML{where}") from e


def load_json(text: str) -> Any:
    """Plain JSON without line numbers (the fallback when the YAML reader rejects it, e.g. tabs)."""
    try:
        return json.loads(text)
    except ValueError as e:
        raise ValueError(f"not valid JSON near line {getattr(e, 'lineno', '?')}") from e
