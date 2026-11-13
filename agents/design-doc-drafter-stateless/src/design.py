"""Pure: the architect's answer (components, flows, contracts, entities, decisions, agents), cleaned in code: unknown requirement ids and unknown dependencies are removed, lists are capped. Name consistency
across flows and contracts is checked later (checks.py)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from llm import off_topic

MAX_COMPONENTS = 20
MAX_ALTERNATIVES = 2
DESIGN_KEYS = ("components", "flows", "contracts", "entities", "decisions", "agents")


def _s(value: Any) -> str:
    return " ".join(str(value or "").split())


def _strs(value: Any) -> list[str]:
    return [_s(v) for v in value if _s(v)] if isinstance(value, list) else []


def _ids(value: Any, known: Iterable[str]) -> list[str]:
    allowed = set(known)
    return list(dict.fromkeys(i.upper() for i in _strs(value) if i.upper() in allowed))


def _dicts(value: Any) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def clean_components(value: Any, ids: Sequence[str]) -> list[dict]:
    comps = [{"name": _s(c.get("name")).replace(" ", ""), "responsibility": _s(c.get("responsibility")),
              "owns": _s(c.get("owns")), "depends_on": _strs(c.get("depends_on")),
              "satisfies": _ids(c.get("satisfies"), ids)} for c in _dicts(value) if _s(c.get("name"))]
    comps = list({c["name"]: c for c in comps}.values())[:MAX_COMPONENTS]
    names = {c["name"] for c in comps}
    return [{**c, "depends_on": [d.replace(" ", "") for d in c["depends_on"]
                                 if d.replace(" ", "") in names and d.replace(" ", "") != c["name"]]}
            for c in comps]


def clean_flows(value: Any) -> list[dict]:
    flows = []
    for f in _dicts(value):
        steps = [{"component": _s(s.get("component")).replace(" ", ""), "action": _s(s.get("action"))}
                 for s in _dicts(f.get("steps")) if _s(s.get("action"))]
        kind = "failure" if _s(f.get("kind")).casefold() == "failure" else "main"
        if steps:
            flows.append({"name": _s(f.get("name")) or kind, "kind": kind, "steps": steps})
    return flows


def clean_contracts(value: Any) -> list[dict]:
    return [{"from": _s(c.get("from")).replace(" ", ""), "to": _s(c.get("to")).replace(" ", ""),
             "purpose": _s(c.get("purpose")), "fields": _strs(c.get("fields")), "errors": _strs(c.get("errors")),
             "notes": _s(c.get("notes"))} for c in _dicts(value) if _s(c.get("from")) and _s(c.get("to"))]


def clean_entities(value: Any) -> list[dict]:
    return [{"name": _s(e.get("name")), "attributes": _strs(e.get("attributes")), "owner": _s(e.get("owner"))}
            for e in _dicts(value) if _s(e.get("name"))]


def clean_decisions(value: Any, ids: Sequence[str]) -> list[dict]:
    return [{"choice": _s(d.get("choice")), "rationale": _s(d.get("rationale")),
             "satisfies": _ids(d.get("satisfies"), ids),
             "alternatives": _strs(d.get("alternatives"))[:MAX_ALTERNATIVES]}
            for d in _dicts(value) if _s(d.get("choice"))]


def clean_agents(value: Any) -> list[dict]:
    return [{"name": _s(a.get("name")), "role": _s(a.get("role")), "tools": _strs(a.get("tools")),
             "permissions": _s(a.get("permissions")) or "read", "state": _s(a.get("state"))}
            for a in _dicts(value) if _s(a.get("name"))]


def clean(data: dict, ids: Sequence[str]) -> dict:
    """Every design key present in `data`, cleaned; absent keys are left out (a revision changes only some)."""
    cleaners = {"components": lambda v: clean_components(v, ids), "flows": clean_flows,
                "contracts": clean_contracts, "entities": clean_entities,
                "decisions": lambda v: clean_decisions(v, ids), "agents": clean_agents}
    return {key: cleaners[key](data[key]) for key in DESIGN_KEYS if key in data}


def validator(ids: Sequence[str]):
    def validate(data: Any) -> dict | None:
        if off_topic(data):
            return {"status": "off_topic"}
        if not isinstance(data, dict):
            return None
        cleaned = clean(data, ids)
        if not cleaned.get("components"):
            return None
        return {"status": "ok", **{k: cleaned.get(k, []) for k in DESIGN_KEYS}}
    return validate

