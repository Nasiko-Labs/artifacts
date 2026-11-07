"""Report items -> tickets. Code first, the model only breaks ties.

(a) explicit refs that exist in the export; (b) else the item name against ticket titles: token Jaccard
>= 0.5 is a match (ties prefer a ticket with children, then the earlier row); (c) items still unmatched
with candidates scoring 0.25 to 0.5 go to the model in ONE call, and its pick is accepted only if it is one
of that item's candidates. Past the deadline (c) is skipped.
"""

from __future__ import annotations

import logging
import re

import llm
import prompts
from llm import LlmError, TurnDeps

log = logging.getLogger("status-report-reality-check.match")

TITLE_MATCH = 0.5
CANDIDATE_MIN = 0.25
MAX_CANDIDATES = 6
MAX_MODEL_ITEMS = 40
MATCH_RESERVE_SECS = 20.0
EXPLICIT, TITLE, MODEL, NONE = "explicit", "title", "model", "none"

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"the", "a", "an", "of", "for", "and", "to", "in", "on", "with", "at", "by", "from", "is", "our",
                   "project", "work", "workstream", "initiative"})


def tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN.findall((text or "").casefold()) if t not in _STOP)


def jaccard(a: str, b: str) -> float:
    left, right = tokens(a), tokens(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def by_code(items: list[dict], tickets: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """(items with explicit/title matches set, {item id: model candidates} for the rest)."""
    by_id = {t["id"].casefold(): t["id"] for t in tickets}
    parents = {t["parent_id"] for t in tickets if t.get("parent_id")}
    matched: list[dict] = []
    pending: dict[str, list[dict]] = {}
    for item in items:
        explicit = list(dict.fromkeys(by_id[r.casefold()] for r in item["refs"] if r.casefold() in by_id))
        if explicit:
            matched.append({**item, "match": {"kind": EXPLICIT, "ids": explicit}})
            continue
        scored = sorted(((jaccard(item["item"], t["title"]), t["id"] in parents, -t["row"], t) for t in tickets),
                        key=lambda s: s[:3], reverse=True)
        if scored and scored[0][0] >= TITLE_MATCH:
            matched.append({**item, "match": {"kind": TITLE, "ids": [scored[0][3]["id"]]}})
            continue
        candidates = [{"id": s[3]["id"], "title": s[3]["title"]} for s in scored
                      if CANDIDATE_MIN <= s[0] < TITLE_MATCH][:MAX_CANDIDATES]
        if candidates:
            pending[item["id"]] = candidates
        matched.append({**item, "match": {"kind": NONE, "ids": []}})
    return matched, pending


async def by_model(deps: TurnDeps, items: list[dict], pending: dict[str, list[dict]],
                   policy: tuple[str, ...]) -> tuple[list[dict], bool]:
    """(items with accepted model picks, whether the deadline fallback ran). A failed call keeps the code
    matches only; it never fails the turn."""
    if not pending:
        return items, False
    if not deps.has_time(MATCH_RESERVE_SECS):
        return items, True
    asked = [i for i in items if i["id"] in pending][:MAX_MODEL_ITEMS]
    block = [{"n": n, "item": i["item"], "candidates": pending[i["id"]]} for n, i in enumerate(asked, start=1)]
    try:
        answer = await llm.complete_json(deps, prompts.match_messages(block, policy), prompts.MATCH_CONTRACT,
                                         _validate_match)
    except LlmError as e:
        log.info("match tie-break skipped: %s", e)
        return items, "deadline" in str(e)
    picks = {}
    for entry in answer["matches"]:
        if 1 <= entry["n"] <= len(asked):
            target = asked[entry["n"] - 1]
            if entry["id"] in {c["id"] for c in pending[target["id"]]}:
                picks[target["id"]] = entry["id"]
    return [{**i, "match": {"kind": MODEL, "ids": [picks[i["id"]]]}} if i["id"] in picks else i
            for i in items], False


def _validate_match(data: object) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        return None
    out = []
    for entry in data["matches"]:
        if isinstance(entry, dict) and isinstance(entry.get("n"), int):
            pick = entry.get("id")
            out.append({"n": entry["n"], "id": pick if isinstance(pick, str) else None})
    return {"matches": out}
