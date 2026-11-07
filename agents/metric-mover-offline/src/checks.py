"""Pure: code checks on the model's hypotheses, then code's ranking.

A hypothesis stands only when its type is in the enum and supported by a candidate, it cites known evidence
ids (event_aligned also a known event id), and every number in it appears in what it cites. Unhedged causal
wording is rewritten ("is caused by" → "may be driven by"); `how_to_confirm` is capped at 40 words and replaced
by the library's generic step when it names a tool the user did not mention. Drops are counted. Code adds the
mandatory measurement-artifact hypothesis when a data-quality trigger fired, and its own wording for the
candidates when nothing the model wrote survived. Order: code score, then type order; at most 6."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import htypes
from candidates import Candidate
from fmt import numbers_in
from text import clean_text, ids_of

MAX_HYPOTHESES = 6
MAX_HOW_WORDS = 40
_CAUSAL = re.compile(r"\b(?:is|was|are|were)\s+(?:caused|driven)\s+by\b|\b(?:caused|driven)\s+by\b|"
                     r"\b(?:is|was)\s+due\s+to\b|\bdue\s+to\b|\bbecause\s+of\b|\bresulted\s+from\b|"
                     r"\b(?:has|have|had)\s+(?:driven|caused|led\s+to)\b|\b(?:drove|caused|led\s+to|leads?\s+to|"
                     r"drives)\b", re.IGNORECASE)
_HEDGE_WORDS = re.compile(r"\b(?:may|might|could|possibly|likely|perhaps|consistent with)\b[^.;]*$", re.IGNORECASE)
_WEAK = ("due", "because", "resulted")
_PASSIVE = re.compile(r"\bby$", re.IGNORECASE)
_UP_WORDS = re.compile(r"\bimprov|\bbetter\b|\bboost", re.IGNORECASE)
_DOWN_WORDS = re.compile(r"\bworse\b|\bworsen|\bdeteriorat", re.IGNORECASE)
HEDGE_WINDOW = 40
VENDORS = ("amplitude", "mixpanel", "google analytics", "ga4", "looker", "tableau", "datadog", "snowflake",
           "bigquery", "segment.io", "heap", "metabase", "dbt", "grafana", "sentry", "jira", "linear", "slack",
           "redshift", "posthog", "optimizely", "launchdarkly", "firebase", "power bi", "hotjar", "excel",
           "splunk", "new relic", "databricks", "fullstory", "statsig", "braze", "hubspot", "salesforce")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Checked:
    hypotheses: tuple[dict, ...]
    dropped: int = 0
    fixed: int = 0          # wording rewritten or a step replaced
    added: int = 0          # hypotheses code added (mandatory or fallback)


def hedge(text: str) -> str:
    """Unhedged causal wording becomes a possibility; wording already hedged in its clause is kept."""
    def repl(match: re.Match) -> str:
        phrase = match.group(0)
        if _HEDGE_WORDS.search(text[max(0, match.start() - HEDGE_WINDOW):match.start()]):
            return phrase
        if phrase.casefold().startswith(_WEAK):
            return "possibly linked to"
        return "may be driven by" if _PASSIVE.search(phrase) else "may have contributed to"
    return _CAUSAL.sub(repl, text)


def contradicts(text: str, effects: Sequence[float]) -> bool:
    """The wording's direction is the opposite of every cited segment's computed change."""
    if not effects:
        return False
    if all(e < 0 for e in effects):
        return bool(_UP_WORDS.search(text))
    return all(e > 0 for e in effects) and bool(_DOWN_WORDS.search(text))


def names_unmentioned_tool(text: str, user_text: str) -> bool:
    lowered, said = f" {text.casefold()} ", (user_text or "").casefold()
    return any(re.search(rf"\b{re.escape(v)}\b", lowered) and v not in said for v in VENDORS)


def check(raw: Any, candidates: Sequence[Candidate], evidence: Mapping[str, str], events: Mapping[str, str],
          user_text: str = "", source: str = "", effects: Mapping[str, float] | None = None) -> Checked:
    kept: list[dict] = []
    dropped = fixed = 0
    for item in raw if isinstance(raw, list) else []:
        result = _one(item, candidates, evidence, events, user_text, source, effects or {})
        if result is None:
            dropped += 1
            continue
        hyp, was_fixed = result
        fixed += was_fixed
        if any(h["type"] == hyp["type"] and h["key"] == hyp["key"] for h in kept):
            continue
        kept.append(hyp)
    added = 0
    by_type = {h["type"] for h in kept}
    fallback = not kept
    for cand in candidates:
        mandatory = cand.type == "measurement_artifact" and cand.triggered and cand.type not in by_type
        if mandatory or fallback:
            kept.append(_from_candidate(cand, evidence))
            added += 1
    return Checked(tuple(rank(kept)), dropped, fixed, added)


def rank(hypotheses: Sequence[dict]) -> list[dict]:
    ordered = sorted(hypotheses, key=lambda h: (-h["score"], htypes.order(h["type"]), h["key"]))[:MAX_HYPOTHESES]
    return [{**h, "rank": n} for n, h in enumerate(ordered, start=1)]


def _one(item: Any, candidates: Sequence[Candidate], evidence: Mapping[str, str], events: Mapping[str, str],
         user_text: str, source: str, effects: Mapping[str, float]) -> tuple[dict, int] | None:
    if not isinstance(item, Mapping):
        return None
    kind = str(item.get("type") or "").strip().casefold().replace(" ", "_").replace("-", "_")
    ev_ids = [i for i in dict.fromkeys(ids_of(item.get("evidence_ids"), "e")) if i in evidence]
    event_ids = [i for i in dict.fromkeys(ids_of(item.get("event_ids"), "ev")) if i in events]
    pool = [c for c in candidates if c.type == kind]
    if kind == "event_aligned":
        pool = [c for c in pool if c.event_id in event_ids]
    text = clean_text(str(item.get("hypothesis") or ""), source)
    if not pool or not ev_ids or not text:
        log.info("hypothesis dropped: type=%s supported=%s ids=%d", kind, bool(pool), len(ev_ids))
        return None
    cited = " ".join([evidence[i] for i in ev_ids] + [events[i] for i in event_ids])
    if not numbers_in(text) <= numbers_in(cited):
        log.info("hypothesis dropped: a number not in its evidence (type=%s)", kind)
        return None
    if contradicts(text, [effects[i] for i in ev_ids if i in effects]):
        log.info("hypothesis dropped: its direction contradicts the evidence (type=%s)", kind)
        return None
    cand = _best(pool, ev_ids, evidence)
    hedged = hedge(text)
    how = " ".join(clean_text(str(item.get("how_to_confirm") or ""), source).split()[:MAX_HOW_WORDS])
    bad_how = not how or names_unmentioned_tool(how, user_text)
    hyp = {"type": kind, "hypothesis": hedged, "evidence_ids": ev_ids, "event_ids": event_ids,
           "evidence": [evidence[i] for i in ev_ids], "how_to_confirm": cand.how if bad_how else how,
           "score": cand.score, "key": f"{cand.dimension}|{cand.segment}|{cand.event_id}", "source": "model"}
    return hyp, int(hedged != text or (bad_how and bool(how)))


def _best(pool: Sequence[Candidate], ev_ids: Sequence[str], evidence: Mapping[str, str]) -> Candidate:
    """The candidate of this type that shares the most cited evidence; ties by score."""
    return max(pool, key=lambda c: (len(set(c.evidence_ids) & set(ev_ids)), c.score))


def _from_candidate(cand: Candidate, evidence: Mapping[str, str]) -> dict:
    ids = [i for i in cand.evidence_ids if i in evidence]
    return {"type": cand.type, "hypothesis": cand.text, "evidence_ids": ids,
            "event_ids": [cand.event_id] if cand.event_id else [], "evidence": [evidence[i] for i in ids],
            "how_to_confirm": cand.how, "score": cand.score,
            "key": f"{cand.dimension}|{cand.segment}|{cand.event_id}", "source": "code"}
