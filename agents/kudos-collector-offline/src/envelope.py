"""Pure: the output envelope (`summary`, `items` = `per_person`, `unattributed`, `caveats`, `confidence`,
`needs_human`). Counts, confidence and needs_human are computed here, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import identity
from write import FALLBACK

HIGH_SHARE, LOW_SHARE, UNATTRIBUTED_SHARE = 0.8, 0.5, 0.25
PERSON_FIELDS = ("name", "aliases", "examples", "counts", "more")


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def stats(result: Mapping[str, Any]) -> dict:
    persons = result["per_person"]
    examples = {e["record_id"]: e for p in persons for e in p["examples"]}
    vague = len(result["vague"])
    countable = max(0, result["records"] - vague)
    attributed = max(0, countable - len(result["unattributed"]))
    return {"records": result["records"], "people": len(persons), "examples": len(examples),
            "unattributed": len(result["unattributed"]), "vague": vague,
            "attributed_share": attributed / countable if countable else 0.0,
            "grounded_share": (sum(1 for e in examples.values() if e["source"] != FALLBACK) / len(examples)
                               if examples else 0.0),
            "unattributed_share": len(result["unattributed"]) / countable if countable else 0.0,
            "ambiguous": sum(1 for p in persons if p["kind"] == identity.FIRST)}


def confidence(result: Mapping[str, Any]) -> str:
    s = stats(result)
    all_ambiguous = s["people"] > 0 and s["ambiguous"] == s["people"]
    if s["attributed_share"] < LOW_SHARE or result.get("deadline_hit") or all_ambiguous:
        return "low"
    if s["attributed_share"] >= HIGH_SHARE and s["grounded_share"] >= HIGH_SHARE:
        return "high"
    return "medium"


def needs_human(result: Mapping[str, Any]) -> bool:
    s = stats(result)
    return s["ambiguous"] > 0 or s["unattributed_share"] > UNATTRIBUTED_SHARE or confidence(result) == "low"


def summary(result: Mapping[str, Any]) -> str:
    s = stats(result)
    text = (f"{_plural(s['records'], 'record')}, {_plural(s['people'], 'person', 'people')}, "
            f"{_plural(s['examples'], 'example')}; {s['unattributed']} unattributed")
    if s["vague"]:
        text += f"; {_plural(s['vague'], 'praise message')} {'was' if s['vague'] == 1 else 'were'} too general to use"
    return text


def _person(p: Mapping[str, Any]) -> dict:
    out = {k: p[k] for k in PERSON_FIELDS if k in p}
    out["examples"] = [{k: v for k, v in e.items() if k != "source"} for e in p["examples"]]
    if p["kind"] == identity.FIRST:
        out["note"] = "first name only; not merged with anyone"
    return out


def build(result: Mapping[str, Any]) -> dict:
    persons = [_person(p) for p in result["per_person"]]
    return {"summary": summary(result), "items": persons, "per_person": persons,
            "unattributed": list(result["unattributed"]), "too_general_praise": list(result["vague"]),
            "caveats": list(result["caveats"]), "confidence": confidence(result), "needs_human": needs_human(result)}
