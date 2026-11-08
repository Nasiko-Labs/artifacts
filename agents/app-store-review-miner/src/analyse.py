"""One analysis turn: select → extract (only reviews not extracted earlier in this conversation) → cluster →
stats → compare → envelope. Model calls happen only in extract and cluster; every number comes from
stats.py / compare.py."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import cluster
import compare
import envelope
import stats
from deps import TurnDeps
from extract import extract
from records import Review
from selection import Selection, select


@dataclass(frozen=True)
class Intake:
    """What the turn starts from: every stored review, the extraction cache, and the input facts."""

    reviews: tuple[Review, ...]
    cache: Mapping[str, dict]
    ours: str
    skipped: int = 0
    paste_cut: bool = False
    coverage: tuple[str, ...] = ()


def _label_counts(reviews: Sequence[Review], cache: Mapping[str, dict], kind: str = "complaints") -> Counter:
    return Counter(label for r in reviews for label in {i["label"] for i in cache.get(r.id, {}).get(kind, ())})


def overview(app: str, reviews: Sequence[Review]) -> dict:
    dates = sorted(r.date for r in reviews if r.date)
    stores = Counter(r.store for r in reviews if r.store)
    return {"app": app, "reviews": len(reviews), "stores": dict(sorted(stores.items())),
            "date_from": dates[0] if dates else None, "date_to": dates[-1] if dates else None,
            "avg_rating": stats.avg_rating(reviews), "rated": sum(r.rating is not None for r in reviews)}


async def run(deps: TurnDeps, intake: Intake, query: str) -> tuple[dict, dict]:
    """(envelope, the updated extraction cache)."""
    sel = select(intake.reviews, query, deps.today, deps.config.max_reviews)
    todo = [r for r in sel.reviews if r.id not in intake.cache]
    extracted = await extract(deps, todo)
    cache = {**intake.cache, **extracted.by_review}
    analysed = [r for r in sel.reviews if r.id in cache]
    apps = list(dict.fromkeys(r.app for r in analysed))
    ours = intake.ours if intake.ours in apps else (apps[0] if apps else intake.ours)
    mine = [r for r in analysed if r.app == ours]
    complaints = {rid: entry.get("complaints", []) for rid, entry in cache.items()}

    our_map = await cluster.cluster(deps, _label_counts(mine, cache))
    our_rt = stats.review_themes(complaints, our_map, mine)
    our_themes, below = stats.themes(ours, mine, our_rt, deps.config)
    names = [t.name for t in our_themes]
    new = stats.new_in(mine, our_rt, names, deps.config)
    our_themes = [replace(t, new_in=new.get(t.name, "")) for t in our_themes]
    result = {
        "overview": overview(ours, mine), "ours": ours,
        "themes": [t.to_dict() for t in our_themes], "below_the_bar": below,
        "by_version": [row.to_dict() for row in stats.version_rows(mine, our_rt, names, deps.config)],
        "praise": [{"label": k, "count": v} for k, v in _label_counts(mine, cache, "praise").most_common(3)],
        "competitor_comparison": [], "competitors": [],
    }
    all_ours = sorted(stats.theme_counts(mine, our_rt))
    for app in [a for a in apps if a != ours]:
        theirs = [r for r in analysed if r.app == app]
        their_map = await cluster.fold(deps, all_ours, _label_counts(theirs, cache))
        their_rt = stats.review_themes(complaints, their_map, theirs)
        their_themes, _ = stats.themes(app, theirs, their_rt, deps.config)
        result["themes"].extend(t.to_dict() for t in their_themes)
        result["competitors"].append({**overview(app, theirs), "small_sample": len(theirs) < deps.config.small_sample})
        result["competitor_comparison"].extend(
            row.to_dict() for row in compare.rows(app, mine, our_rt, theirs, their_rt, deps.config))
    checks = _checks(deps, intake, sel, extracted, analysed)
    return envelope.build(result, checks, _caveats(deps, intake, sel, checks)), cache


def _checks(deps: TurnDeps, intake: Intake, sel: Selection, extracted, analysed: Sequence[Review]) -> dict:
    return {"parsed": len(intake.reviews), "skipped": intake.skipped, "selected": len(sel.reviews),
            "analysed": len(analysed), "not_analysed": len(extracted.not_analysed), "capped": sel.capped,
            "excluded_by_window": sel.excluded_by_window, "undated_in_window": sel.undated_in_window,
            "window_start": sel.window_start.isoformat() if sel.window_start else None,
            "returned": extracted.returned, "dropped": extracted.dropped, "salvage": deps.budget.exhausted,
            "coverage_gap": bool(intake.coverage), "paste_cut": intake.paste_cut, "llm_calls": deps.budget.calls}


def _caveats(deps: TurnDeps, intake: Intake, sel: Selection, c: Mapping) -> list[str]:
    notes = [f"{c['parsed']} reviews parsed, {c['skipped']} skipped (no text), {c['analysed']} analysed."]
    if c["paste_cut"]:
        notes.append(f"The paste was cut at {deps.config.paste_limit:,} characters; reviews after that were not read.")
    if sel.window_start:
        notes.append(f"Window {sel.window_start.isoformat()}..{deps.today.isoformat()}: {sel.excluded_by_window} "
                     f"reviews outside it excluded, {sel.undated_in_window} undated reviews kept.")
    if sel.capped:
        notes.append(f"Capped at {deps.config.max_reviews} reviews (newest first, then lowest rating): "
                     f"{sel.capped} not analysed.")
    if c["not_analysed"] or c["salvage"]:
        notes.append(f"Step limit: {c['not_analysed']} reviews could not be read in time or budget.")
    if c["dropped"]:
        notes.append(f"{c['dropped']} of {c['returned']} extracted items failed the verbatim check and were dropped.")
    return [*notes, *intake.coverage]
