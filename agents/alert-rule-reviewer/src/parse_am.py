"""Pure: Alertmanager config (`route`, `receivers`, `inhibit_rules`) into `AmConfig`. Matchers are read
from all three spellings (`match`, `match_re`, `matchers`); a child route inherits its parent's receiver
and grouping, as Alertmanager does."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import exprs
from alerts import ALERTMANAGER, AmConfig, InhibitRule, Matcher, Parsed, Receiver, Route

_MATCHER = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(=~|!~|!=|=)\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(.*?))\s*$""")


def is_alertmanager(doc: Any) -> bool:
    return isinstance(doc, Mapping) and isinstance(doc.get("route"), Mapping) and "receivers" in doc


def matchers(node: Mapping, prefix: str = "") -> tuple[Matcher, ...]:
    found: list[Matcher] = []
    for name, value in _mapping(node.get(f"{prefix}match")).items():
        found.append(Matcher(str(name), "=", str(value)))
    for name, value in _mapping(node.get(f"{prefix}match_re")).items():
        found.append(Matcher(str(name), "=~", str(value)))
    for text in node.get(f"{prefix}matchers") or ():
        parsed = _MATCHER.match(str(text))
        if parsed:
            value = next(v for v in (parsed.group(3), parsed.group(4), parsed.group(5), "") if v is not None)
            found.append(Matcher(parsed.group(1), parsed.group(2), value))
    return tuple(found)


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _route(node: Mapping, path: str, parent: Route | None) -> Route:
    group_by = node.get("group_by")
    inherited = parent.group_by if parent is not None else None
    route = Route(
        path=path,
        receiver=str(node.get("receiver") or (parent.receiver if parent else "")),
        matchers=matchers(node),
        line=getattr(node, "line", 0),
        group_by=tuple(str(g) for g in group_by) if isinstance(group_by, list) else inherited,
        group_wait_s=exprs.duration_secs(node.get("group_wait")),
        group_interval_s=exprs.duration_secs(node.get("group_interval")),
        repeat_interval_s=exprs.duration_secs(node.get("repeat_interval")),
        keep_going=bool(node.get("continue")),
        key_lines=dict(getattr(node, "key_lines", {}) or {}),
    )
    children = tuple(_route(child, f"{path}.routes[{i}]", route)
                     for i, child in enumerate(node.get("routes") or ()) if isinstance(child, Mapping))
    return Route(**{**route.__dict__, "children": children})


def _receiver(node: Mapping) -> Receiver:
    kinds = tuple(k for k, v in node.items() if k.endswith("_configs") and v)
    return Receiver(name=str(node.get("name") or ""), line=getattr(node, "line", 0), kinds=kinds)


def _inhibit(node: Mapping) -> InhibitRule:
    return InhibitRule(source=matchers(node, "source_"), target=matchers(node, "target_"),
                       equal=tuple(str(e) for e in node.get("equal") or ()), line=getattr(node, "line", 0))


def parse(doc: Any, file: str, parsed: Parsed) -> bool:
    if not is_alertmanager(doc):
        return False
    config = AmConfig(
        file=file,
        root=_route(doc["route"], "route", None),
        receivers=tuple(_receiver(r) for r in doc.get("receivers") or () if isinstance(r, Mapping)),
        inhibit=tuple(_inhibit(r) for r in doc.get("inhibit_rules") or () if isinstance(r, Mapping)),
        line=getattr(doc["route"], "line", 0),
    )
    parsed.am.append(config)
    parsed.add_format(ALERTMANAGER)
    return True
