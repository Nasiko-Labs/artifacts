"""Pure: caveats, `confidence`, `needs_human` and the output envelope, all computed in code from the batch."""

from __future__ import annotations

from collections.abc import Sequence

from model import LEXICON_ONLY, MODEL, NOT_ANALYSED, Ticket, public
from rules import PRIORITIES

KNOWN_SHARE_FOR_HIGH = 0.70
LOW_UNANALYSED_SHARE = 0.25
POLICY_CAVEAT = ("Priorities assume enterprise and business tiers are the SLA-critical ones; paste an `SLA:` line "
                 "to check ages against your targets.")


def caveats(tickets: Sequence[Ticket], skipped: int = 0, duplicates: int = 0, dropped_quotes: int = 0,
            has_sla: bool = False) -> list[str]:
    n = len(tickets)
    notes = []
    unknown_tier = sum(1 for t in tickets if t.tier == "unknown")
    if unknown_tier:
        notes.append(f"Tier unknown for {unknown_tier} of {n} tickets ({_pct(unknown_tier, n)}).")
    no_date = sum(1 for t in tickets if t.age_hours is None)
    if no_date:
        notes.append(f"No readable created date for {no_date} of {n} tickets, so their age isn't used.")
    generated = [t.id for t in tickets if t.generated_id]
    if generated:
        notes.append(f"No ids given for {len(generated)} tickets; I numbered them {generated[0]}..{generated[-1]}.")
    for state in (LEXICON_ONLY, NOT_ANALYSED):
        ids = [t.label for t in tickets if t.analysis == state]
        if ids:
            notes.append(f"{_state_text(state)}: {', '.join(ids[:20])}{' …' if len(ids) > 20 else ''}.")
    if skipped:
        notes.append(f"Skipped {skipped} rows with neither a subject nor a body.")
    if duplicates:
        notes.append(f"{duplicates} duplicate ids: the later row was used.")
    if dropped_quotes:
        noun = "signal" if dropped_quotes == 1 else "signals"
        notes.append(f"Left out {dropped_quotes} {noun} whose quote wasn't found in the ticket.")
    if not has_sla:
        notes.append(POLICY_CAVEAT)
    return notes


def _state_text(state: str) -> str:
    if state == LEXICON_ONLY:
        return "Triaged from keyword signals only (lexicon-only)"
    return "Not analysed by the model (keyword signals only, category other)"


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def confidence(tickets: Sequence[Ticket]) -> str:
    n = len(tickets) or 1
    unanalysed = sum(1 for t in tickets if t.analysis != MODEL)
    if unanalysed / n > LOW_UNANALYSED_SHARE:
        return "low"
    tiers_known = sum(1 for t in tickets if t.tier != "unknown") / n
    dates_known = sum(1 for t in tickets if t.age_hours is not None) / n
    if unanalysed == 0 and tiers_known >= KNOWN_SHARE_FOR_HIGH and dates_known >= KNOWN_SHARE_FOR_HIGH:
        return "high"
    return "medium"


def needs_human(tickets: Sequence[Ticket], level: str) -> bool:
    flagged = any(t.priority == "P1" or t.has("legal_threat") or t.has("security_concern") for t in tickets)
    return flagged or level == "low"


def counts(tickets: Sequence[Ticket]) -> dict:
    tally = {p: sum(1 for t in tickets if t.priority == p) for p in PRIORITIES}
    return {**tally, "high_risk": sum(1 for t in tickets if t.escalation_risk == "high")}


def summary(tickets: Sequence[Ticket]) -> str:
    c = counts(tickets)
    split = ", ".join(f"{c[p]} {p}" for p in PRIORITIES)
    return f"{len(tickets)} tickets: {split}; {c['high_risk']} high escalation risk"


def envelope(all_tickets: Sequence[Ticket], shown: Sequence[Ticket], notes: Sequence[str]) -> dict:
    """`summary`/`counts`/`confidence`/`needs_human` describe the whole batch; `items` = `tickets` = shown."""
    level = confidence(all_tickets)
    rows = [public(t) for t in shown]
    return {"summary": summary(all_tickets), "items": rows, "tickets": rows, "counts": counts(all_tickets),
            "caveats": list(notes), "confidence": level, "needs_human": needs_human(all_tickets, level)}
