"""Pure: configuration documents (Terraform/HCL, `terraform state show`, YAML incl. multi-document and
`kind: List`, JSON) into named bodies with a line number for every attribute path, and the pairing of
IaC documents with live ones.

A Terraform document is named by its address (`aws_security_group.api`), a Kubernetes object by
`kind/name` (`deployment/payments-api`), anything else by its file path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import yaml

import hcl_lite

Path = tuple[str | int, ...]
_HCL_EXT = (".tf", ".hcl", ".tfvars")
_HCL_START = re.compile(r"(?m)^\s*(?:resource|data|module)\s+\"|^#\s+\S+:\s*$")
_LOADER_SCALAR = yaml.SafeLoader("")


@dataclass(frozen=True)
class Doc:
    name: str
    body: Any
    lines: dict = field(default_factory=dict)  # path inside body → 1-based line
    line: int = 1
    namespace: str = ""

    def line_of(self, path: Path) -> int:
        """The line of the attribute, else of its nearest parent that has one, else of the document."""
        for cut in range(len(path), 0, -1):
            if path[:cut] in self.lines:
                return self.lines[path[:cut]]
        return self.line


class Unreadable(ValueError):
    pass


def parse_docs(text: str, path: str = "") -> tuple[Doc, ...]:
    """Every document in `text`; raises Unreadable when nothing can be read."""
    body = (text or "").strip("\n")
    if not body.strip():
        raise Unreadable("empty")
    if path.endswith(_HCL_EXT) or (not path.endswith((".json", ".yaml", ".yml")) and _HCL_START.search(body)):
        return _hcl(body, path)
    if body.lstrip()[:1] in "{[":
        try:
            return _json(body, path)
        except ValueError:
            pass
    return _yaml(body, path)


def _hcl(text: str, path: str) -> tuple[Doc, ...]:
    if path.endswith(".tfvars"):
        attrs, lines = hcl_lite.parse_attributes(text)
        return (Doc(path, attrs, lines),) if attrs else _none()
    blocks = [b for b in hcl_lite.parse(text) if b.kind in ("resource", "data", "module")]
    if not blocks:
        _none()
    return tuple(Doc(b.address, b.body, dict(b.lines), b.line) for b in blocks)


def _none():
    raise Unreadable("no blocks")


def _json(text: str, path: str) -> tuple[Doc, ...]:
    data = json.loads(text)
    data = _unwrap(data)
    items = data.get("items") if isinstance(data, dict) and data.get("kind") == "List" else None
    bodies = items if isinstance(items, list) else (data if isinstance(data, list) else [data])
    docs = []
    cursor = 0
    for body in bodies:
        lines: dict = {}
        cursor = _json_lines(text, body, (), lines, cursor)
        docs.append(_named(body, path, lines, 1))
    return tuple(docs)


def _unwrap(data: Any) -> Any:
    """A describe-style wrapper `{"SecurityGroups": [ {...} ]}` holding one object is unwrapped."""
    if isinstance(data, dict) and len(data) == 1:
        (value,) = data.values()
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            return value[0]
    return data


def _json_lines(text: str, node: Any, path: Path, lines: dict, cursor: int) -> int:
    if isinstance(node, dict):
        for key, value in node.items():
            at = text.find(json.dumps(key), cursor)
            if at != -1:
                lines[(*path, key)] = text.count("\n", 0, at) + 1
                cursor = at + 1
            cursor = _json_lines(text, value, (*path, key), lines, cursor)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            cursor = _json_lines(text, value, (*path, i), lines, cursor)
    return cursor


def _yaml(text: str, path: str) -> tuple[Doc, ...]:
    try:
        nodes = [n for n in yaml.compose_all(text, Loader=yaml.SafeLoader) if n is not None]
    except yaml.YAMLError as e:
        raise Unreadable(str(e)) from e
    docs = []
    for node in nodes:
        lines: dict = {}
        body = _node(node, (), lines)
        if isinstance(body, dict) and body.get("kind") == "List" and isinstance(body.get("items"), list):
            for i, item in enumerate(body["items"]):
                sub = {p[2:]: n for p, n in lines.items() if p[:2] == ("items", i) and len(p) > 2}
                docs.append(_named(item, path, sub, lines.get(("items", i), 1)))
        elif isinstance(body, (dict, list)):
            docs.append(_named(body, path, lines, node.start_mark.line + 1))
    if not docs:
        raise Unreadable("no mapping")
    return tuple(docs)


def _node(node: yaml.Node, path: Path, lines: dict) -> Any:
    if isinstance(node, yaml.MappingNode):
        out = {}
        for key_node, value_node in node.value:
            key = plain(_LOADER_SCALAR.construct_object(key_node, deep=True))
            lines[(*path, key)] = key_node.start_mark.line + 1
            out[key] = _node(value_node, (*path, key), lines)
        return out
    if isinstance(node, yaml.SequenceNode):
        items = []
        for i, item in enumerate(node.value):
            lines[(*path, i)] = item.start_mark.line + 1
            items.append(_node(item, (*path, i), lines))
        return items
    return plain(_LOADER_SCALAR.construct_object(node, deep=True))


def plain(value: Any) -> Any:
    """JSON-safe: dates and timestamps become their ISO text."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, hcl_lite.Unresolved):
        return value.text
    return value


def _named(body: Any, path: str, lines: dict, line: int) -> Doc:
    if isinstance(body, dict) and isinstance(body.get("kind"), str) and isinstance(body.get("metadata"), dict):
        meta = body["metadata"]
        name = f"{body['kind'].casefold()}/{meta.get('name') or '?'}"
        return Doc(name, body, lines, line, str(meta.get("namespace") or ""))
    return Doc(path or "config", body, lines, line)


def pair(iac: tuple[Doc, ...], live: tuple[Doc, ...]) -> tuple[tuple[Doc, Doc], ...]:
    """Each live document with the IaC document of the same name; one of each pairs regardless."""
    if len(iac) == 1 and len(live) == 1:
        return ((iac[0], live[0]),)
    by_name = {d.name.casefold(): d for d in iac}
    return tuple((by_name[d.name.casefold()], d) for d in live if d.name.casefold() in by_name)
