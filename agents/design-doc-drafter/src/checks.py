"""Pure: the code checks on a draft. Gaps a repair call may fix (unknown component names in flows and
contracts, functional requirements without an acceptance criterion), what remains after it (dropped and
counted), and grounding of every model-written string: numbers the input never gave become [TBD], unnamed
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


def structural_gaps(design: dict, sections: dict, reqs: Sequence[dict]) -> list[str]:
    known = names(design)
    gaps: list[str] = []
    for flow in design.get("flows") or ():
        bad = sorted({s["component"] for s in flow["steps"] if s["component"] not in known})
        if bad:
            gaps.append(f"flow '{flow['name']}' names unknown components {', '.join(bad)}")
    for c in design.get("contracts") or ():
        bad = [n for n in (c["from"], c["to"]) if n not in known]
        if bad:
            gaps.append(f"contract {c['from']} -> {c['to']} names unknown components {', '.join(bad)}")
    missing = unverified(sections, reqs)
    if missing:
        gaps.append(f"no acceptance criterion verifies {', '.join(missing)}: add one per id")
    return gaps


def unverified(sections: dict, reqs: Sequence[dict]) -> list[str]:
    verified = {i for c in sections.get("acceptance_criteria") or () for i in c["verifies"]}
    return [r["id"] for r in reqs if r["type"] == "functional" and r["id"] not in verified]


def uncovered(design: dict, reqs: Sequence[dict]) -> list[str]:
    covered = {i for c in design.get("components") or () for i in c["satisfies"]}
    return [r["id"] for r in reqs if r["type"] != "non_goal" and r["id"] not in covered]


def apply_repair(design: dict, sections: dict, fixed: dict) -> tuple[dict, dict]:
    """New design and sections with the repaired flows/contracts replaced and criteria appended."""
    new_design = {**design, **{k: fixed[k] for k in ("flows", "contracts") if fixed.get(k)}}
    criteria = [*(sections.get("acceptance_criteria") or ()), *(fixed.get("acceptance_criteria") or ())]
    return new_design, {**sections, "acceptance_criteria": criteria}


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
