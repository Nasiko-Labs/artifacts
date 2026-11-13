"""Pure: the code checks on a draft. Unknown component names in flows and contracts (dropped and counted),
functional requirements without an acceptance criterion or a component (open questions later), and grounding of every model-written string: numbers the input never gave become [TBD], unnamed
technologies become generic nouns. Decision choices keep their technology (they render as "Proposed")."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from grounding import (
    generic_tech,
    ground_numbers,
    ground_text_numbers,
    named_tech,
    normalise,
    tech_in_ground,
)

SKIP_KEYS = frozenset({"name", "component", "from", "to", "owner", "verifies", "satisfies", "depends_on", "kind"})


def names(design: dict) -> set[str]:
    return {c["name"] for c in design.get("components") or ()}


def unverified(sections: dict, reqs: Sequence[dict]) -> list[str]:
    verified = {i for c in sections.get("acceptance_criteria") or () for i in c["verifies"]}
    return [r["id"] for r in reqs if r["type"] == "functional" and r["id"] not in verified]


def uncovered(design: dict, reqs: Sequence[dict]) -> list[str]:
    covered = {i for c in design.get("components") or () for i in c["satisfies"]}
    return [r["id"] for r in reqs if r["type"] != "non_goal" and r["id"] not in covered]


def drop_unknown(design: dict) -> tuple[dict, int]:
    """Flow steps and contracts that still name an unknown component are dropped; the count is reported."""
    known = names(design)
    dropped = 0
    flows = []
    for flow in design.get("flows") or ():
        steps = [s for s in flow["steps"] if s["component"] in known]
        dropped += len(flow["steps"]) - len(steps)
        if steps:
            flows.append({**flow, "steps": steps})
    contracts = [c for c in design.get("contracts") or () if c["from"] in known and c["to"] in known]
    dropped += len(design.get("contracts") or ()) - len(contracts)
    entities = [{**e, "owner": e["owner"] if e["owner"] in known else ""} for e in design.get("entities") or ()]
    return {**design, "flows": flows, "contracts": contracts, "entities": entities}, dropped


@dataclass(frozen=True)
class Grounded:
    design: dict
    sections: dict
    tbd_units: tuple[str, ...]
    generic: tuple[str, ...]


def ground(design: dict, sections: dict, ground_text: str) -> Grounded:
    norm = normalise(ground_text)
    numbers = ground_numbers(norm)
    proposed = [d["choice"] for d in design.get("decisions") or ()]
    units: list[str] = []
    generic: list[str] = []

    def fix(value: Any, key: str = "", keep_tech: bool = False) -> Any:
        if isinstance(value, dict):
            return {k: fix(v, k, keep_tech or k in ("choice", "alternatives")) for k, v in value.items()}
        if isinstance(value, list):
            return [fix(v, key, keep_tech) for v in value]
        if not isinstance(value, str) or key in SKIP_KEYS:
            return value
        text, missing = ground_text_numbers(value, numbers)
        units.extend(missing)
        if not keep_tech:
            text, replaced = generic_tech(text, norm, proposed)
            generic.extend(replaced)
        return text

    return Grounded(fix(design), fix(sections), tuple(units), tuple(dict.fromkeys(generic)))


def decision_given(choice: str, ground_text: str) -> bool:
    """A decision is given (not proposed) when its technology, or its whole wording, is in the input."""
    norm = normalise(ground_text)
    techs = named_tech(choice)
    if techs:
        return all(tech_in_ground(t, norm) for t in techs)
    return bool(normalise(choice)) and normalise(choice) in norm
