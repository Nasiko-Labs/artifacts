"""Churn signals: a lexicon hit in code first, then the model confirms it is the customer's own intent.
No lexicon hit is never a signal, whatever the model says. The quote is the item's own text."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

import llm
import prompts
from batching import batches
from items import FeedbackItem
from quotes import cut_quote

MAX_CHURN_CALLS = 5
KINDS = ("cancel", "switch", "downgrade", "renewal_risk", "frustration")
_LEXICON = re.compile(
    r"\bcancel(?:l?ing|led|lation)?\b|\bnot renew|\bwon'?t renew|\bwill not renew|\bswitch(?:ing)? to\b|"
    r"\bmoving to\b|\bevaluating (?:alternatives|competitors)\b|\bleaving\b|\blast straw\b|\bfed up\b|"
    r"\brefund|\bdowngrad|\bcontract (?:ends|expires)\b|\bescalat", re.IGNORECASE)
_LOOKING_AT = re.compile(r"\b[Ll]ooking at [A-Z][\w-]+")


class _Signal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    is_churn_signal: bool = False
    kind: str = "frustration"


class _Signals(BaseModel):
    model_config = ConfigDict(extra="ignore")
    signals: list[_Signal]


@dataclass(frozen=True)
class Checked:
    flagged: tuple[dict, ...]   # {"item_id", "kind"}
    unchecked: int = 0          # lexicon hits the model could not confirm (call failed or over budget)


def _validate(data: object) -> dict | None:
    try:
        return _Signals.model_validate(data).model_dump()
    except ValidationError:
        return None


def lexicon_hit(text: str) -> bool:
    return bool(_LEXICON.search(text or "") or _LOOKING_AT.search(text or ""))


async def confirm(deps: llm.TurnDeps, items: Sequence[FeedbackItem], context: Sequence[str] = ()) -> Checked:
    candidates = [i for i in items if lexicon_hit(i.text)]
    groups = batches(candidates)
    asked = groups[:MAX_CHURN_CALLS]

    def job(group: list[FeedbackItem]):
        async def run() -> dict | None:
            try:
                return await llm.complete_json(deps, prompts.churn_messages(group, context), prompts.CHURN_CONTRACT,
                                               _validate)
            except llm.LlmError:
                return None
        return run

    results, _ = await llm.bounded([job(g) for g in asked], deps.remaining() - 10.0)
    flagged: list[dict] = []
    unchecked = sum(len(g) for g in groups[MAX_CHURN_CALLS:])
    for group, result in zip(asked, results, strict=True):
        if result is None:
            unchecked += len(group)
            continue
        ids = {i.id for i in group}
        for entry in result["signals"]:
            if entry["is_churn_signal"] and entry["id"] in ids and entry["id"] not in {f["item_id"] for f in flagged}:
                flagged.append({"item_id": entry["id"], "kind": entry["kind"] if entry["kind"] in KINDS
                                else "frustration"})
    return Checked(tuple(flagged), unchecked)


def signals(flagged: Sequence[dict], items: Sequence[FeedbackItem]) -> list[dict]:
    """Output rows for flagged items still in `items`, ordered by ARR (desc), then date (desc)."""
    by_id = {i.id: i for i in items}
    rows = [{"customer": by_id[f["item_id"]].customer or "unknown", "kind": f["kind"],
             "quote": cut_quote(by_id[f["item_id"]].text), "arr": by_id[f["item_id"]].arr,
             "date": by_id[f["item_id"]].date, "item_id": f["item_id"]}
            for f in flagged if f["item_id"] in by_id]
    newest = sorted(rows, key=lambda r: r["date"], reverse=True)
    return sorted(newest, key=lambda r: -(r["arr"] or 0.0))
