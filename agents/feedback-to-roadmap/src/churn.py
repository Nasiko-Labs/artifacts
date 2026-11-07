"""Churn signals: a lexicon hit in code first, then the model confirms it is the customer's own intent (one
call per <= 40 candidates, at most 5 calls). No lexicon hit is never a signal, whatever the model says. The
quote is the item's own text; ARR, date and url come from the item."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
import records
from deps import TurnDeps
from extract import chunks
from pool import bounded
from quotes import cut_quote
from reader import ask_once
from records import Item, RawSignals

MAX_CHURN_CALLS = 5
CANDIDATES_PER_CALL = 40
KINDS = ("cancel", "switch", "downgrade", "renewal_risk", "frustration")
_LEXICON = re.compile(
    r"\bcancel(?:l?ing|led|lation)?\b|\bnot renew|\bwon'?t renew|\bwill not renew|\bswitch(?:ing)? to\b|"
    r"\bmoving to\b|\bevaluating (?:alternatives|competitors)\b|\bleaving\b|\blast straw\b|\bfed up\b|"
    r"\brefund|\bdowngrad|\bcontract (?:ends|expires)\b|\bescalat", re.IGNORECASE)
_LOOKING_AT = re.compile(r"\b[Ll]ooking at [A-Z][\w-]+")


@dataclass(frozen=True)
class Checked:
    flagged: tuple[dict, ...]   # {"item_id", "kind"}
    unchecked: int = 0          # lexicon hits the model could not confirm
    cut: bool = False


def lexicon_hit(text: str) -> bool:
    return bool(_LEXICON.search(text or "") or _LOOKING_AT.search(text or ""))


async def confirm(deps: TurnDeps, items: Sequence[Item]) -> Checked:
    candidates = [i for i in items if lexicon_hit(i.text)]
    groups = chunks(candidates, CANDIDATES_PER_CALL)
    asked = groups[:MAX_CHURN_CALLS]

    def job(group: list[Item]):
        async def run() -> RawSignals | None:
            return records.parse(RawSignals, await ask_once(deps, prompts.churn_messages(group)))
        return run

    ran = await bounded([job(g) for g in asked], deps.config.concurrency)
    flagged: list[dict] = []
    unchecked = sum(len(g) for g in groups[MAX_CHURN_CALLS:])
    for group, parsed in zip(asked, ran.results, strict=True):
        if parsed is None:
            unchecked += len(group)
            continue
        ids = {i.id for i in group}
        for entry in parsed.signals:
            if entry.is_churn_signal and entry.id in ids and entry.id not in {f["item_id"] for f in flagged}:
                flagged.append({"item_id": entry.id, "kind": entry.kind if entry.kind in KINDS else "frustration"})
    return Checked(tuple(flagged), unchecked, ran.cut)


def signals(flagged: Sequence[dict], items: Sequence[Item]) -> list[dict]:
    """Output rows for flagged items, ordered by ARR (desc), then date (desc)."""
    by_id = {i.id: i for i in items}
    rows = [{"customer": by_id[f["item_id"]].customer or "unknown", "kind": f["kind"],
             "quote": cut_quote(by_id[f["item_id"]].text), "arr": by_id[f["item_id"]].arr,
             "date": by_id[f["item_id"]].date, "url": by_id[f["item_id"]].url, "item_id": f["item_id"]}
            for f in flagged if f["item_id"] in by_id]
    newest = sorted(rows, key=lambda r: r["date"], reverse=True)
    return sorted(newest, key=lambda r: -(r["arr"] or 0.0))
