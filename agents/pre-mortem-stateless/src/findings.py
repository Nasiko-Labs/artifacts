"""Pure: reading the crew's two answers into checked data.

The finder's answer becomes candidates (ids are the finder's own `f<n>` when they are well formed and unique,
else positional; lenses outside the four default by position). The skeptic's answer can only re-rate a known
id with valid levels and rewrite the early warning / mitigation of a known id; anything else it says about
stories is ignored. The code checks in `specific.py` run after both."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from rank import LENS_ORDER
from specific import LEVELS, Candidate, from_model

MAX_CANDIDATES = 14
OK, OFF_TOPIC = "ok", "off_topic"
_ID = re.compile(r"^f\d{1,2}$")


def read_find(data: Any) -> tuple[str, list[Candidate]] | None:
    """(status, candidates), or None when the answer does not match the contract."""
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return OFF_TOPIC, []
    modes = data.get("failure_modes")
    if data.get("status") not in (OK, None) or not isinstance(modes, list):
        return None
    raws = [m for m in modes if isinstance(m, dict)][:MAX_CANDIDATES]
    ids = [str(m.get("id") or "").strip().casefold() for m in raws]
    usable = all(_ID.match(i) for i in ids) and len(set(ids)) == len(ids)
    built = []
    for n, raw in enumerate(raws, start=1):
        lens = str(raw.get("lens") or "").strip().casefold()
        lens = lens if lens in LENS_ORDER else LENS_ORDER[(n - 1) % len(LENS_ORDER)]
        built.append(from_model(raw, ids[n - 1] if usable else f"f{n}", lens))
    return OK, [c for c in built if c is not None]


def read_review(data: Any) -> dict | None:
    """The skeptic's lists, or None when the answer does not match the contract (or it declined)."""
    if not isinstance(data, dict) or data.get("status") == OFF_TOPIC:
        return None
    keys = ("ratings", "fixes", "merge_groups", "unstated_assumptions", "questions_for_the_team")
    lists = {k: data.get(k) or [] for k in keys}
    return lists if all(isinstance(v, list) for v in lists.values()) else None


def apply_review(candidates: list[Candidate], review: dict | None) -> list[Candidate]:
    """Each candidate with the skeptic's valid rating and field fixes applied."""
    if not review:
        return candidates
    ratings = _by_id(review["ratings"])
    fixes = _by_id(review["fixes"])
    out = []
    for c in candidates:
        rating, fix = ratings.get(c.id, {}), fixes.get(c.id, {})
        likelihood = _level(rating.get("likelihood")) or c.likelihood
        impact = _level(rating.get("impact")) or c.impact
        warning = " ".join(str(fix.get("early_warning") or "").split()) or c.early_warning
        mitigation = " ".join(str(fix.get("mitigation") or "").split()) or c.mitigation
        out.append(replace(c, likelihood=likelihood, impact=impact, early_warning=warning, mitigation=mitigation))
    return out


def _by_id(items: list) -> dict[str, dict]:
    return {str(i.get("id") or "").strip().casefold(): i for i in items if isinstance(i, dict)}


def _level(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return text if text in LEVELS else ""
