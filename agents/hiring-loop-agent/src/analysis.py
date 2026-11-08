"""Pure: the debrief's analysis, all in code. Never an average, a sum, a count of yes votes, a leaning
or a rank: points exist only to find spreads.

    C1 recommendation  ratings spread >= SPREAD_FLAG, or a negative and a positive with one of them strong
    C2 competency      the same competency rated by two or more interviewers with a spread >= 2
    C3 claim           opposite-polarity claims on one topic from different interviewers, labelled by the
                       model (prefiltered here, at most MAX_PAIRS)
    C4 pair            two interviewers who sat the same interview, rated >= 2 apart
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from protected_terms import personal_term, sentences, vague_term
from ratings import display, normalise
from records import (
    TOPICS,
    Agreement,
    Block,
    Claim,
    Contradiction,
    FeedbackRecord,
    Gap,
    QualityFlag,
    Quote,
)

SPREAD_FLAG = 3.0
COMPETENCY_SPREAD = 2.0
PAIR_SPREAD = 2.0
AGREE_WITHIN = 1.0
MAX_PAIRS = 12
CONCRETE_MIN_WORDS = 8

SYNONYMS: Mapping[str, str] = {
    "architecture": "system design", "system architecture": "system design", "design": "system design",
    "systems design": "system design", "system design": "system design",
    "collaboration": "communication", "teamwork": "communication", "communication skills": "communication",
    "programming": "coding", "implementation": "coding", "code quality": "coding", "coding": "coding",
    "algorithms": "coding", "problem solving": "coding", "live coding": "coding",
    "troubleshooting": "debugging", "debugging": "debugging",
    "accountability": "ownership", "initiative": "ownership", "ownership": "ownership",
    "mentoring": "leadership", "leadership": "leadership",
    "domain expertise": "domain knowledge", "domain": "domain knowledge", "domain knowledge": "domain knowledge",
    "motivation": "motivation", "interest": "motivation",
}
_NEGATIVE = re.compile(r"\b(?:did not|didn'?t|could not|couldn'?t|can'?t|cannot|was not|wasn'?t|were not|"
                       r"not|no|never|struggled|struggles|weak|unclear|failed|missed|poor|wrong|lacked|lacking|"
                       r"unable|confused|incomplete|without)\b", re.IGNORECASE)
_POSITIVE = re.compile(r"\b(?:strong|clear|clearly|solved|did|could|good|great|excellent|well|correct|correctly|"
                       r"handled|considered|thorough|quickly|complete|completed|nailed|solid|implemented|"
                       r"explained|designed|identified|fixed)\b", re.IGNORECASE)
_PAST_TENSE = re.compile(r"\b\w{3,}ed\b|\b(?:wrote|built|said|gave|took|made|ran|chose|drew|found|knew|saw|"
                         r"spent|led|got|went|did|began|thought|told)\b", re.IGNORECASE)
_CODE_TERMS = re.compile(r"\b(?:function|query|cache|api|sql|test|tests|class|loop|queue|index|schema|thread|"
                         r"lock|hash|map|array|endpoint|latency|database|retry|retries|bug|stack|o\(n)\b",
                         re.IGNORECASE)
_DIGIT = re.compile(r"\d")


def competency_key(name: str) -> str:
    flat = " ".join(re.sub(r"[^a-z ]+", " ", (name or "").casefold()).split())
    return SYNONYMS.get(flat, flat)


def topic_of(raw: str) -> str:
    flat = competency_key(raw)
    return flat if flat in TOPICS else "other"


def alphabetical(records: Iterable[FeedbackRecord]) -> tuple[FeedbackRecord, ...]:
    return tuple(sorted(records, key=lambda r: r.interviewer.casefold()))


def is_concrete(text: str) -> bool:
    """Heuristic: a sentence of >= 8 words with a past-tense verb, a number or a code term."""
    return any(len(s.split()) >= CONCRETE_MIN_WORDS and (_PAST_TENSE.search(s) or _DIGIT.search(s) or
                                                        _CODE_TERMS.search(s)) for s in sentences(text))


def polarity(text: str) -> int:
    if _NEGATIVE.search(text or ""):
        return -1
    return 1 if _POSITIVE.search(text or "") else 0


def _names(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _first_quote(record: FeedbackRecord, key: str | None = None) -> Quote | None:
    for comp in record.competencies:
        if key is None or comp.name == key:
            for text in (*comp.concern_quotes, *comp.evidence_quotes):
                return Quote(record.interviewer, text, record.source)
    if key is None and record.claims:
        return Quote(record.interviewer, record.claims[0].quote, record.source)
    return None


@dataclass(frozen=True)
class ClaimPair:
    id: str
    topic: str
    left: tuple[str, Claim]
    right: tuple[str, Claim]
    sources: tuple[str, str] = ("pasted", "pasted")


def recommendation_split(records: Sequence[FeedbackRecord]) -> Contradiction | None:
    rated = [(r, normalise(r.rating_text)) for r in alphabetical(records)]
    rated = [(r, p) for r, p in rated if p is not None]
    if len(rated) < 2:
        return None
    points = [p for _, p in rated]
    spread = max(points) - min(points)
    opposite = min(points) <= -1 and max(points) >= 1 and max(abs(min(points)), max(points)) >= 2
    if spread < SPREAD_FLAG and not opposite:
        return None
    names = tuple(r.interviewer for r, _ in rated)
    quotes = tuple(q for q in (_first_quote(r) for r, _ in rated) if q is not None)
    return Contradiction(
        id="c1", kind="recommendation", competency="recommendation", interviewers=names, quotes=quotes,
        ratings=tuple((r.interviewer, display(r.rating_text)) for r, _ in rated), spread=round(spread, 2),
        suggested_question=f"Ask {_names(names)} what each saw that led to their rating. Which answers or code "
                           "support it?")


def competency_conflicts(records: Sequence[FeedbackRecord]) -> tuple[Contradiction, ...]:
    by_key: dict[str, list[tuple[FeedbackRecord, str, float]]] = {}
    for record in alphabetical(records):
        for comp in record.competencies:
            point = normalise(comp.rating_text)
            if point is not None:
                by_key.setdefault(comp.name, []).append((record, comp.rating_text, point))
    found = []
    for key in sorted(by_key):
        entries = _one_per_interviewer(by_key[key])
        if len(entries) < 2:
            continue
        spread = max(p for *_, p in entries) - min(p for *_, p in entries)
        if spread < COMPETENCY_SPREAD:
            continue
        names = tuple(r.interviewer for r, *_ in entries)
        quotes = tuple(q for q in (_first_quote(r, key) for r, *_ in entries) if q is not None)
        found.append(Contradiction(
            id=f"c2-{len(found) + 1}", kind="competency", competency=key, interviewers=names, quotes=quotes,
            ratings=tuple((r.interviewer, display(text)) for r, text, _ in entries), spread=round(spread, 2),
            suggested_question=f"Ask {_names(names)} what each saw in {key}. What answer or code supports the rating?"))
    return tuple(found)


def _one_per_interviewer(entries: list) -> list:
    seen: dict[str, tuple] = {}
    for entry in entries:
        seen.setdefault(entry[0].interviewer.casefold(), entry)
    return list(seen.values())


def pair_interview_conflicts(records: Sequence[FeedbackRecord]) -> tuple[Contradiction, ...]:
    by_interview: dict[str, list[FeedbackRecord]] = {}
    for record in alphabetical(records):
        if record.interview.strip():
            by_interview.setdefault(" ".join(record.interview.casefold().split()), []).append(record)
    found = []
    for interview in sorted(by_interview):
        for a, b in combinations(by_interview[interview], 2):
            pa, pb = normalise(a.rating_text), normalise(b.rating_text)
            if pa is None or pb is None or abs(pa - pb) < PAIR_SPREAD:
                continue
            names = (a.interviewer, b.interviewer)
            quotes = tuple(q for q in (_first_quote(a), _first_quote(b)) if q is not None)
            found.append(Contradiction(
                id=f"c4-{len(found) + 1}", kind="pair", competency=a.interview, interviewers=names, quotes=quotes,
                ratings=((a.interviewer, display(a.rating_text)), (b.interviewer, display(b.rating_text))),
                spread=round(abs(pa - pb), 2),
                suggested_question=f"{a.interviewer} and {b.interviewer} sat the same {a.interview} interview but "
                                   "rated it apart. Ask each what they weighed."))
    return tuple(found)


def claim_pairs(records: Sequence[FeedbackRecord]) -> tuple[tuple[ClaimPair, ...], int]:
    """(opposite-polarity pairs from different interviewers on one topic, capped at MAX_PAIRS; how many were
    left out by the cap)."""
    by_topic: dict[str, list[tuple[FeedbackRecord, Claim]]] = {}
    for record in alphabetical(records):
        for claim in record.claims:
            by_topic.setdefault(claim.topic, []).append((record, claim))
    pairs: list[ClaimPair] = []
    for topic in sorted(by_topic):
        for (ra, ca), (rb, cb) in combinations(by_topic[topic], 2):
            if ra.interviewer.casefold() == rb.interviewer.casefold():
                continue
            if {polarity(ca.quote), polarity(cb.quote)} != {-1, 1}:
                continue
            pairs.append(ClaimPair(f"p{len(pairs) + 1}", topic, (ra.interviewer, ca), (rb.interviewer, cb),
                                   (ra.source, rb.source)))
    return tuple(pairs[:MAX_PAIRS]), max(0, len(pairs) - MAX_PAIRS)


def claim_conflicts(pairs: Sequence[ClaimPair], labels: Mapping[str, str],
                    already: Sequence[Contradiction]) -> tuple[Contradiction, ...]:
    """Only pairs the model labelled `conflict`; one per (topic, interviewers) not already flagged by C2."""
    taken = {(c.competency, frozenset(c.interviewers)) for c in already}
    found = []
    for pair in pairs:
        if labels.get(pair.id) != "conflict":
            continue
        names = (pair.left[0], pair.right[0])
        key = (pair.topic, frozenset(names))
        if key in taken:
            continue
        taken.add(key)
        found.append(Contradiction(
            id=f"c3-{len(found) + 1}", kind="claim", competency=pair.topic, interviewers=names,
            quotes=(Quote(pair.left[0], pair.left[1].quote, pair.sources[0]),
                    Quote(pair.right[0], pair.right[1].quote, pair.sources[1])),
            suggested_question=f"Ask {names[0]} and {names[1]} what the candidate actually said or did on "
                               f"{pair.topic}."))
    return tuple(found)


def agreements(records: Sequence[FeedbackRecord]) -> tuple[Agreement, ...]:
    by_key: dict[str, list[tuple[FeedbackRecord, str, float, tuple[str, ...]]]] = {}
    for record in alphabetical(records):
        for comp in record.competencies:
            point = normalise(comp.rating_text)
            if point is not None:
                by_key.setdefault(comp.name, []).append((record, comp.rating_text, point, comp.evidence_quotes))
    found = []
    for key in sorted(by_key):
        entries = _one_per_interviewer(by_key[key])
        points = [p for _, _, p, _ in entries]
        if len(entries) < 2 or max(points) - min(points) > AGREE_WITHIN or not all(q for *_, q in entries):
            continue
        first = entries[0]
        found.append(Agreement(key, tuple((r.interviewer, display(t)) for r, t, _, _ in entries),
                               Quote(first[0].interviewer, first[3][0], first[0].source)))
    return tuple(found)


def gaps(records: Sequence[FeedbackRecord], expected: Sequence[str], panel: Sequence[str]) -> tuple[Gap, ...]:
    covered = {c.name for r in records for c in r.competencies} | {c.topic for r in records for c in r.claims}
    found = [Gap("uncovered_competency", key, f"Not covered by anyone: {key}.")
             for key in dict.fromkeys(competency_key(e) for e in expected) if key not in covered]
    for record in alphabetical(records):
        if normalise(record.rating_text) is None:
            found.append(Gap("no_rating", record.interviewer, f"{record.interviewer} gave no rating I could read."))
        if not record.has_evidence:
            found.append(Gap("no_evidence", record.interviewer,
                             f"{record.interviewer}'s feedback has no concrete example (an opinion only; heuristic)."))
    names = {r.interviewer.casefold() for r in records}
    for person in panel:
        if not _panel_match(person, names):
            found.append(Gap("missing_interviewer", person, f"No feedback from {person}."))
    return tuple(found)


def _panel_match(person: str, names: set[str]) -> bool:
    wanted = person.casefold().split()
    return any(set(wanted) <= set(name.split()) or name.split()[0] == wanted[0] for name in names if wanted)


PERSONAL_PROMPT = "references a personal characteristic; recommend it be disregarded and the interviewer reminded"
VAGUE_PROMPT = "Ask for the behaviour or answer this was based on."


def quality_flags(blocks: Sequence[Block]) -> tuple[QualityFlag, ...]:
    """Per sentence of each interviewer's feedback: a personal-characteristic mention wins over vague wording."""
    found = []
    for block in sorted(blocks, key=lambda b: b.interviewer.casefold()):
        for sentence in sentences(block.text):
            personal = personal_term(sentence)
            if personal:
                found.append(QualityFlag("personal", block.interviewer, sentence, personal, PERSONAL_PROMPT))
                continue
            vague = vague_term(sentence)
            if vague:
                found.append(QualityFlag("vague", block.interviewer, sentence, vague, VAGUE_PROMPT))
    return tuple(found)
