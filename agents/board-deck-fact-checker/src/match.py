"""Matching: code shortlists at most 12 candidate cells per claim; one tool-less model call per 10 claims
picks among them (or proposes a formula over them); code rejects any cell id outside the shortlist."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence

import checks
import prompts
import shortlist
from deps import TurnDeps
from records import Cell, Claim, Match
from steps import ask_once

log = logging.getLogger("board_deck.match")

CLAIMS_PER_CALL = 10
TIME_LIMIT = "not checked (time limit)"


async def run(deps: TurnDeps, claims: Sequence[Claim], cells: Sequence[Cell]) -> dict[str, Match]:
    shortlists = {c.claim_id: shortlist.candidates(c, cells) for c in claims}
    matches = {c.claim_id: Match(c.claim_id, note="no matching cell in the source")
               for c in claims if not shortlists[c.claim_id]}
    pending = [c for c in claims if shortlists[c.claim_id]]
    for start in range(0, len(pending), CLAIMS_PER_CALL):
        batch = pending[start:start + CLAIMS_PER_CALL]
        if deps.past_deadline():
            matches.update({c.claim_id: Match(c.claim_id, note=TIME_LIMIT) for c in batch})
            continue
        matches.update(await _batch(deps, batch, shortlists))
    return matches


async def _batch(deps: TurnDeps, batch: Sequence[Claim], shortlists: Mapping[str, Sequence[Cell]]) -> dict:
    payload = json.dumps({"claims": [_claim_payload(c, shortlists[c.claim_id]) for c in batch]}, ensure_ascii=False)
    messages = ({"role": "system", "content": prompts.match_system()},
                {"role": "user", "content": prompts.match_user(payload)})
    answer = checks.parse_answer(await ask_once(deps, messages))
    by_id = {checks.text_of(i.get("claim_id")): i for i in checks.items(answer, "matches")}
    if answer is None:
        log.info("unreadable match answer for %d claims", len(batch))
    return {c.claim_id: _checked(by_id.get(c.claim_id), c, shortlists[c.claim_id]) for c in batch}


def _checked(item, claim: Claim, candidates: Sequence[Cell]) -> Match:
    checked = checks.check_match(item, claim, frozenset(cell.cell_id for cell in candidates))
    if checked.cell_ids or checked.op:
        return checked
    fit = shortlist.sole_fit(claim, candidates)
    if fit is None:
        return checked
    log.info("claim %s: model chose nothing valid; using the only same-label, same-period cell", claim.claim_id)
    return Match(claim.claim_id, cell_ids=(fit.cell_id,), reason="the only candidate with the same label and period")


def _claim_payload(claim: Claim, candidates: Sequence[Cell]) -> dict:
    return {"claim_id": claim.claim_id, "slide": claim.slide, "quote": claim.quote, "label": claim.label,
            "value_text": claim.value_text, "period": claim.period, "kind": claim.kind,
            "candidates": [{"cell_id": c.cell_id, "address": c.address, "spreadsheet": c.spreadsheet,
                            "row_label": c.row_label, "column_label": c.col_label, "value": c.text}
                           for c in candidates]}
