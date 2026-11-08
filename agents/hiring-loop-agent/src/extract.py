"""The model calls that read feedback: intake (is this in scope, who is the candidate), the fallback split
of unstructured paste, one transcription per interviewer, and labels for prefiltered claim pairs.

Everything the model returns is checked here: a quote, rating, name or competency is kept only when it
appears verbatim in that interviewer's text; anything about a personal characteristic is not kept."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import prompts
from analysis import ClaimPair, competency_key, is_concrete, topic_of
from checks import Verbatim, items, text_of
from deps import TurnDeps
from protected_terms import personal_term
from ratings import is_rating
from records import Block, Claim, Competency, FeedbackRecord
from steps import ask_json

log = logging.getLogger("hiring_loop.extract")

CHUNK_CHARS = 12_000
PAIRS_PER_CALL = 4
MAX_PAIR_CALLS = 3
_LABELS = frozenset({"conflict", "consistent", "unrelated"})


@dataclass(frozen=True)
class Intake:
    off_topic: bool
    candidate: str = ""


async def intake(deps: TurnDeps, text: str) -> Intake:
    answer = await ask_json(deps, prompts.INTAKE_SYSTEM, text) or {}
    if str(answer.get("status") or "").casefold() == prompts.OFF_TOPIC.casefold():
        return Intake(True)
    candidate = text_of(answer, "candidate")
    return Intake(False, candidate if candidate and Verbatim(text).has_phrase(candidate) else "")


@dataclass(frozen=True)
class Split:
    off_topic: bool
    blocks: tuple[Block, ...] = ()
    candidate: str = ""


async def split(deps: TurnDeps, text: str) -> Split:
    """One call that splits unstructured feedback; every value is checked against the pasted text."""
    answer = await ask_json(deps, prompts.SPLIT_SYSTEM, prompts.fence(text)) or {}
    if str(answer.get("status") or "").casefold() == prompts.OFF_TOPIC.casefold():
        return Split(True)
    source = Verbatim(text)
    blocks = []
    for raw in items(answer, "blocks"):
        name = text_of(raw, "interviewer")
        body = source.kept_sentences(str(raw.get("text") or ""))
        if not name or not source.has_phrase(name) or not body:
            continue
        rating = text_of(raw, "rating_text")
        interview = text_of(raw, "interview")
        blocks.append(Block(interviewer=name, text=body, interview=interview if source.has_phrase(interview) else "",
                            rating_text=rating if source.has_phrase(rating) and is_rating(rating) else ""))
    candidate = text_of(answer, "candidate")
    return Split(False, tuple(blocks), candidate if source.has_phrase(candidate) else "")


def chunks(text: str, size: int = CHUNK_CHARS) -> tuple[str, ...]:
    """Paragraph-aligned pieces of at most `size` characters (a longer paragraph is cut hard)."""
    pieces: list[str] = []
    current = ""
    for para in (text or "").split("\n"):
        joined = f"{current}\n{para}" if current else para
        if len(joined) <= size:
            current = joined
            continue
        if current:
            pieces.append(current)
        while len(para) > size:
            pieces.append(para[:size])
            para = para[size:]
        current = para
    if current.strip():
        pieces.append(current)
    return tuple(p for p in pieces if p.strip()) or ("",)


async def transcribe(deps: TurnDeps, block: Block) -> FeedbackRecord:
    """One call per chunk of this interviewer's feedback; the parts are merged in code."""
    record = FeedbackRecord(interviewer=block.interviewer, interview=block.interview, rating_text=block.rating_text,
                            source=block.source, source_url=block.source_url, has_evidence=is_concrete(block.text))
    for piece in chunks(block.text):
        if not piece.strip() or deps.past_deadline():
            continue
        header = f"Interviewer: {block.interviewer}\nInterview: {block.interview or 'not stated'}\n"
        answer = await ask_json(deps, prompts.EXTRACT_SYSTEM, header + prompts.fence(piece))
        if answer is None:
            log.info("an interviewer's feedback could not be transcribed")
            continue
        record = _merge(record, answer, Verbatim(piece))
    return record


def _merge(record: FeedbackRecord, answer: Mapping, source: Verbatim) -> FeedbackRecord:
    dropped = 0
    competencies = list(record.competencies)
    for raw in items(answer, "competencies"):
        evidence, lost_e = _quotes(raw.get("evidence_quotes"), source)
        concerns, lost_c = _quotes(raw.get("concern_quotes"), source)
        dropped += lost_e + lost_c
        label = text_of(raw, "name")
        rating = text_of(raw, "rating_text")
        rating = rating if source.has_phrase(rating) and is_rating(rating) else ""
        if not label or not (evidence or concerns or rating):
            continue
        competencies = _add_competency(competencies, Competency(competency_key(label), label, rating, evidence,
                                                                concerns))
    claims = list(record.claims)
    for raw in items(answer, "claims"):
        quote = text_of(raw, "quote")
        if not source.has(quote) or personal_term(quote):
            dropped += 1
            continue
        claims.append(Claim(topic_of(text_of(raw, "topic")), quote, text_of(raw, "statement")))
    overall = record.rating_text
    recommendation = text_of(answer, "recommendation_text")
    if not overall and source.has_phrase(recommendation) and is_rating(recommendation):
        overall = recommendation
    return replace(record, rating_text=overall, competencies=tuple(competencies), claims=tuple(claims),
                   dropped_quotes=record.dropped_quotes + dropped)


def _quotes(raw: object, source: Verbatim) -> tuple[tuple[str, ...], int]:
    values = [" ".join(v.split()) for v in raw if isinstance(v, str)] if isinstance(raw, list) else []
    kept = tuple(dict.fromkeys(v for v in values if source.has(v) and not personal_term(v)))
    return kept, len(values) - len(kept)


def _add_competency(existing: list[Competency], new: Competency) -> list[Competency]:
    for n, comp in enumerate(existing):
        if comp.name == new.name:
            merged = replace(comp, rating_text=comp.rating_text or new.rating_text,
                             evidence_quotes=tuple(dict.fromkeys((*comp.evidence_quotes, *new.evidence_quotes))),
                             concern_quotes=tuple(dict.fromkeys((*comp.concern_quotes, *new.concern_quotes))))
            return [*existing[:n], merged, *existing[n + 1:]]
    return [*existing, new]


async def label_pairs(deps: TurnDeps, pairs: Sequence[ClaimPair]) -> dict[str, str]:
    """`conflict | consistent | unrelated` per pair id; batched, at most MAX_PAIR_CALLS calls."""
    labels: dict[str, str] = {}
    batches = [pairs[i:i + PAIRS_PER_CALL] for i in range(0, len(pairs), PAIRS_PER_CALL)][:MAX_PAIR_CALLS]
    for batch in batches:
        if deps.past_deadline():
            break
        payload = [{"id": p.id, "topic": p.topic, "a": {"interviewer": p.left[0], "statement": p.left[1].quote},
                    "b": {"interviewer": p.right[0], "statement": p.right[1].quote}} for p in batch]
        answer = await ask_json(deps, prompts.PAIR_SYSTEM, prompts.fence(json.dumps(payload, ensure_ascii=False)))
        ids = {p.id for p in batch}
        for raw in items(answer or {}, "pairs"):
            label = str(raw.get("label") or "").casefold()
            if raw.get("id") in ids and label in _LABELS:
                labels = {**labels, str(raw["id"]): label}
    return labels
