"""Pure: code decides the recommendation's basis; the model only words it.

1. A user override ("recommend B instead") or a stated preference wins.
2. Else, with criteria, the top-scored option; a top-two gap under 5 points is a close call.
3. Else the option with the fewest cons tied to the user's constraints (then fewest cons, then input order)."""

from __future__ import annotations

import criteria as crit
from brief import Brief
from grounding import JUDGEMENT
from text import normalise


def _tied_cons(brief: Brief, cons: list[dict]) -> int:
    lines = [normalise(c) for c in brief.constraints]
    return sum(1 for c in cons if c["basis"] != JUDGEMENT and any(normalise(c["basis"]) in line or line in
                                                                  normalise(c["basis"]) for line in lines))


def decide(brief: Brief, analysis: dict, override: str = "") -> dict:
    keys = [o.key for o in brief.options]
    cons = {k: len(analysis["cons"].get(k) or ()) for k in keys}
    ranking = crit.rank(keys, analysis["ratings"], brief.criteria, cons) if brief.criteria else None
    if override:
        kind, option = "override", override
    elif brief.preference:
        kind, option = "preference", brief.preference
    elif ranking is not None:
        kind, option = "criteria", ranking.ranked[0]
    else:
        order = sorted(range(len(keys)), key=lambda i: (_tied_cons(brief, analysis["cons"].get(keys[i]) or []),
                                                        cons[keys[i]], i))
        kind, option = "constraints", keys[order[0]]
    if ranking is not None:
        others = [k for k in ranking.ranked if k != option]
    else:
        others = sorted((k for k in keys if k != option), key=lambda k: (cons[k], keys.index(k)))
    return {"kind": kind, "option": option, "runner_up": others[0] if others else "",
            "close_call": bool(ranking and kind == "criteria" and ranking.close_call),
            "scores": dict(ranking.scores) if ranking else {}, "ranked": list(ranking.ranked) if ranking else []}


def model_view(brief: Brief, basis: dict, instruction: str = "") -> dict:
    """What the write step is told: the decided option and basis, never asked to choose."""
    name = {o.key: o.name for o in brief.options}
    view = {"recommended_option": basis["option"], "recommended_name": name.get(basis["option"], ""),
            "basis": basis["kind"], "best_alternative": basis["runner_up"],
            "scores": basis["scores"] or None, "close_call": basis["close_call"],
            "criteria_weights": {c.name: round(c.weight) for c in brief.criteria} or None,
            "constraints": list(brief.constraints)}
    if instruction:
        view["user_revision_request"] = instruction
    return view
