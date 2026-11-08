"""Report items -> tickets. Code first; the model only breaks ties (copied method).

(a) explicit refs (issue keys as written) that exist in the pulled issues; (b) else the item name against
ticket titles: token Jaccard >= 0.5 is a match (ties prefer a parent, then pull order); (c) items still
unmatched with candidates scoring 0.25 to 0.5 go to the model, at most 20 items per call, and a pick is
accepted only if it is one of that item's offered candidates. Past the deadline (c) is skipped.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence

from records import Issue

log = logging.getLogger("weekly_rollup.linking")

TITLE_MATCH = 0.5
CANDIDATE_MIN = 0.25
MAX_CANDIDATES = 6
ITEMS_PER_CALL = 20
MAX_MODEL_CALLS = 2
EXPLICIT, TITLE, MODEL, NONE = "explicit", "title", "model", "none"

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"the", "a", "an", "of", "for", "and", "to", "in", "on", "with", "at", "by", "from", "is", "our",
                   "project", "work", "workstream", "initiative"})

AskJson = Callable[[list[dict]], Awaitable[dict | None]]


def tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN.findall((text or "").casefold()) if t not in _STOP)


def jaccard(a: str, b: str) -> float:
    left, right = tokens(a), tokens(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def by_code(items: Sequence[dict], issues: Sequence[Issue]) -> tuple[list[dict], dict[str, list[dict]]]:
    """(items with explicit/title matches set, {item id: model candidates} for the rest)."""
    by_key = {i.key.casefold(): i.key for i in issues}
    parents = {i.parent_key for i in issues if i.parent_key}
    matched: list[dict] = []
    pending: dict[str, list[dict]] = {}
    for item in items:
        explicit = list(dict.fromkeys(by_key[r.casefold()] for r in item["refs"] if r.casefold() in by_key))
        if explicit:
            matched.append({**item, "match": {"kind": EXPLICIT, "ids": explicit}})
            continue
        scored = sorted(((jaccard(item["item"], i.title), i.key in parents, -n, i) for n, i in enumerate(issues)),
                        key=lambda s: s[:3], reverse=True)
        if scored and scored[0][0] >= TITLE_MATCH:
            matched.append({**item, "match": {"kind": TITLE, "ids": [scored[0][3].key]}})
            continue
        candidates = [{"id": s[3].key, "title": s[3].title} for s in scored
                      if CANDIDATE_MIN <= s[0] < TITLE_MATCH][:MAX_CANDIDATES]
        if candidates:
            pending[item["id"]] = candidates
        matched.append({**item, "match": {"kind": NONE, "ids": []}})
    return matched, pending


async def by_model(ask: AskJson, build_messages: Callable[[list[dict]], list[dict]], items: list[dict],
                   pending: dict[str, list[dict]], has_time: Callable[[], bool]) -> tuple[list[dict], bool]:
    """(items with accepted model picks, whether the deadline fallback ran). A failed call keeps the code
    matches only; it never fails the turn."""
    asked = [i for i in items if i["id"] in pending][: ITEMS_PER_CALL * MAX_MODEL_CALLS]
    picks: dict[str, str] = {}
    fallback = False
    for start in range(0, len(asked), ITEMS_PER_CALL):
        if not has_time():
            fallback = True
            break
        batch = asked[start:start + ITEMS_PER_CALL]
        block = [{"n": n, "item": i["item"], "candidates": pending[i["id"]]} for n, i in enumerate(batch, start=1)]
        picks = {**picks, **_accepted(await ask(build_messages(block)), batch, pending)}
    if picks:
        log.info("model tie-break accepted %d of %d items", len(picks), len(asked))
    return [{**i, "match": {"kind": MODEL, "ids": [picks[i["id"]]]}} if i["id"] in picks else i
            for i in items], fallback


def _accepted(answer: dict | None, batch: list[dict], pending: dict[str, list[dict]]) -> dict[str, str]:
    raw = answer.get("matches") if isinstance(answer, dict) else None
    out: dict[str, str] = {}
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("n"), int) or not isinstance(entry.get("id"), str):
            continue
        if 1 <= entry["n"] <= len(batch):
            target = batch[entry["n"] - 1]
            offered = {c["id"]: c["title"] for c in pending[target["id"]]}
            if entry["id"] in offered and jaccard(target["item"], offered[entry["id"]]) >= CANDIDATE_MIN:
                out[target["id"]] = entry["id"]
    return out
