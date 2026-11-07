"""Pure: HCL blocks to resources. `var.x` / `local.x` take their literal defaults; `count` and `for_each`
over a literal (or resolved) list or map multiply the resource; anything unresolvable is assumed 1 and
listed as an assumption."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import resources as rs
from parse_hcl import HclDoc

_EXACT = re.compile(r"^\$\{(var|local)\.([\w-]+)\}$")
_INTERP = re.compile(r"\$\{(var|local)\.([\w-]+)\}")
_SET_OF = re.compile(r"^\$\{(?:toset\()?\[(.*)\]\)?\}$", re.DOTALL)
_SET_OF_REF = re.compile(r"^\$\{toset\((var|local)\.([\w-]+)\)\}$")
_QUOTED = re.compile(r'"[^"]*"')
LOCAL_PASSES = 3


@dataclass
class Expanded:
    resources: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    default_tags: dict = field(default_factory=dict)
    region: str = ""
    multiplied: int = 0
    unresolved_counts: int = 0


def resolve(value: Any, scope: dict) -> Any:
    if isinstance(value, dict):
        return {k: resolve(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, scope) for v in value]
    if not isinstance(value, str) or rs.UNRESOLVED not in value:
        return value
    exact = _EXACT.match(value) or _SET_OF_REF.match(value)
    if exact:
        found = scope.get(exact.group(1), {}).get(exact.group(2), value)
        return value if found is value else found

    def sub(match: re.Match) -> str:
        found = scope.get(match.group(1), {}).get(match.group(2))
        return str(found) if isinstance(found, (str, int, float)) and not rs.is_unresolved(found) else match.group(0)
    return _INTERP.sub(sub, value)


def expand(doc: HclDoc) -> Expanded:
    scope: dict = {"var": dict(doc.variables), "local": {}}
    for _ in range(LOCAL_PASSES):
        scope["local"] = {k: resolve(v, scope) for k, v in doc.locals.items()}
    out = Expanded()
    for name, body in doc.providers:
        if name == "aws":
            _provider(resolve(body, scope), out)
    for rtype, name, body in doc.resources:
        out.resources.append(_resource(rtype, name, resolve(body, scope), out))
    for name, source in doc.modules:
        out.resources.append(rs.make(f"module.{name}", "module", "module", {"source": source}, {}, False))
        out.assumptions.append(f"Module `{name}` ({source or 'no source'}) is not fetched: its resources are unpriced.")
    return out


def _provider(body: dict, out: Expanded) -> None:
    region = rs.text(body.get("region"))
    if region and not out.region:
        out.region = region
    blocks = body.get("default_tags")
    for block in blocks if isinstance(blocks, list) else [blocks] if isinstance(blocks, dict) else []:
        tags = block.get("tags") if isinstance(block, dict) else None
        if isinstance(tags, dict):
            out.default_tags.update({str(k): str(v) for k, v in tags.items()})


def _resource(rtype: str, name: str, body: dict, out: Expanded) -> dict:
    address = f"{rtype}.{name}"
    qty = 1
    for key in ("count", "for_each"):
        if key not in body:
            continue
        out.multiplied += 1
        n = _how_many(body[key])
        if n is None:
            out.unresolved_counts += 1
            out.assumptions.append(f"{key} of {address} not resolvable: assumed 1.")
        else:
            qty = n
    tags = body.get("tags")
    tags_known = isinstance(tags, dict) or tags is None
    attrs = {k: v for k, v in body.items() if k not in ("count", "for_each", "tags", "tags_all")}
    return rs.make(address, rtype, rtype, attrs, tags if isinstance(tags, dict) else {}, tags_known, qty)


def _how_many(value: Any) -> int | None:
    if isinstance(value, (list, dict)):
        return len(value)
    number = rs.to_number(value)
    if number is not None:
        return max(int(number), 0)
    if isinstance(value, str):
        listed = _SET_OF.match(value)
        if listed and rs.UNRESOLVED not in listed.group(1) and "var." not in listed.group(1):
            return len(_QUOTED.findall(listed.group(1))) or len([x for x in listed.group(1).split(",") if x.strip()])
    return None
