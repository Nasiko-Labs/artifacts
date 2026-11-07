"""Pure: the two Terraform resource types this agent reviews (`datadog_monitor`,
`aws_cloudwatch_metric_alarm`), read with python-hcl2 and, when that fails, with a small regex reader for
flat attributes and one level of nested blocks. Line numbers always come from the text itself."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

_RESOURCE = re.compile(r'^\s*resource\s+"(datadog_monitor|aws_cloudwatch_metric_alarm)"\s+"([^"]+)"\s*\{')
_ATTR = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")
_BLOCK = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*$")
_KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|\{)")


@dataclass(frozen=True)
class Resource:
    type: str
    name: str
    attrs: dict[str, Any]
    line: int
    key_lines: dict[str, int] = field(default_factory=dict)


def resources(text: str) -> tuple[list[Resource], list[str]]:
    """(resources, notes). A resource whose braces never close is named in a note and skipped."""
    lines = text.split("\n")
    found, notes = [], []
    for start, line in enumerate(lines):
        match = _RESOURCE.match(line)
        if not match:
            continue
        end = _block_end(lines, start)
        if end is None:
            notes.append(f'resource "{match.group(1)}" "{match.group(2)}" (line {start + 1}): unbalanced braces, '
                         "skipped")
            continue
        body = "\n".join(lines[start:end + 1])
        attrs = _with_hcl2(body, match.group(1), match.group(2))
        if attrs is None:
            attrs = _with_regex(lines[start + 1:end])
        found.append(Resource(match.group(1), match.group(2), attrs, start + 1, _key_lines(lines, start, end)))
    return found, notes


def _block_end(lines: list[str], start: int) -> int | None:
    depth = 0
    for i in range(start, len(lines)):
        stripped = re.sub(r'"(?:[^"\\]|\\.)*"', '""', lines[i])
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0 and i > start:
            return i
        if depth <= 0 and "{" in stripped and "}" in stripped:
            return i
    return None


def _key_lines(lines: list[str], start: int, end: int) -> dict[str, int]:
    keys: dict[str, int] = {}
    for i in range(start + 1, end):
        match = _KEY.match(lines[i])
        if match:
            keys.setdefault(match.group(1), i + 1)
    return keys


def _unquote(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) >= 2 and value[0] == value[-1] == '"':
            return value[1:-1].replace('\\"', '"').replace("\\n", "\n")
        return value
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], dict) and value[0].get("__is_block__"):
            return _unquote(value[0])
        return [_unquote(v) for v in value]
    if isinstance(value, dict):
        return {_unquote(k): _unquote(v) for k, v in value.items() if k != "__is_block__"}
    return value


def _with_hcl2(body: str, kind: str, name: str) -> dict | None:
    try:
        import hcl2

        tree = _unquote(hcl2.loads(body))
    except Exception:  # noqa: BLE001 - any parser failure falls back to the regex reader
        return None
    for resource in tree.get("resource") or ():
        attrs = (resource.get(kind) or {}).get(name)
        if isinstance(attrs, dict):
            return attrs
    return None


def _literal(raw: str) -> Any:
    text = raw.strip().rstrip(",")
    if text in ("true", "false"):
        return text == "true"
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def _with_regex(lines: list[str]) -> dict:
    attrs: dict[str, Any] = {}
    nested: dict | None = None
    for line in lines:
        if nested is not None and line.strip() == "}":
            nested = None
            continue
        block = _BLOCK.match(line)
        if block and nested is None:
            nested = attrs.setdefault(block.group(1), {})
            continue
        match = _ATTR.match(line)
        if match:
            (nested if nested is not None else attrs)[match.group(1)] = _literal(match.group(2))
    return attrs
