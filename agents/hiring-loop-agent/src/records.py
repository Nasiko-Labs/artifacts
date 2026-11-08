"""The data model. Frozen dataclasses; every record is built by code from checked input.

    Block            one interviewer's raw feedback (pasted, or transcribed from an email or a document)
    FeedbackRecord   what extraction kept for one interviewer, every quote verified verbatim
    Contradiction    a conflict to resolve in the debrief, with both sides quoted
    Gap / QualityFlag what nobody covered / feedback that is vague or mentions a personal characteristic
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

TOPICS: tuple[str, ...] = ("coding", "system design", "debugging", "communication", "ownership", "leadership",
                           "domain knowledge", "motivation", "other")
Kind = Literal["recommendation", "competency", "claim", "pair"]


@dataclass(frozen=True)
class Block:
    interviewer: str
    text: str
    interview: str = ""
    rating_text: str = ""
    source: str = "pasted"        # pasted | email | document
    source_url: str = ""
    date: str = ""


@dataclass(frozen=True)
class Competency:
    name: str                     # normalised (synonyms mapped)
    label: str                    # as the interviewer named it
    rating_text: str = ""
    evidence_quotes: tuple[str, ...] = ()
    concern_quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Claim:
    topic: str
    quote: str
    statement: str = ""


@dataclass(frozen=True)
class FeedbackRecord:
    interviewer: str
    interview: str
    rating_text: str              # overall, as written ("" = not stated)
    competencies: tuple[Competency, ...] = ()
    claims: tuple[Claim, ...] = ()
    source: str = "pasted"
    source_url: str = ""
    dropped_quotes: int = 0
    has_evidence: bool = True     # heuristic, from the block text (analysis.is_concrete)


@dataclass(frozen=True)
class Quote:
    interviewer: str
    text: str
    source: str = "pasted"


@dataclass(frozen=True)
class Contradiction:
    id: str
    kind: Kind
    competency: str
    interviewers: tuple[str, ...]
    quotes: tuple[Quote, ...]
    ratings: tuple[tuple[str, str], ...] = ()   # (interviewer, rating as written)
    spread: float = 0.0
    suggested_question: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["interviewers"] = list(self.interviewers)
        data["quotes"] = [asdict(q) for q in self.quotes]
        data["ratings"] = [{"interviewer": i, "rating_text": r} for i, r in self.ratings]
        return data


@dataclass(frozen=True)
class Gap:
    kind: Literal["uncovered_competency", "no_rating", "no_evidence", "missing_interviewer"]
    subject: str
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class QualityFlag:
    kind: Literal["vague", "personal"]
    interviewer: str
    quote: str
    term: str
    prompt: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Agreement:
    competency: str
    ratings: tuple[tuple[str, str], ...]
    quote: Quote

    def to_dict(self) -> dict:
        return {"competency": self.competency, "ratings": [{"interviewer": i, "rating_text": r} for i, r in self.ratings],
                "quote": asdict(self.quote)}


@dataclass(frozen=True)
class DebriefReport:
    candidate: str
    role: str
    records: tuple[FeedbackRecord, ...]
    contradictions: tuple[Contradiction, ...] = ()
    agreements: tuple[Agreement, ...] = ()
    gaps: tuple[Gap, ...] = ()
    quality_flags: tuple[QualityFlag, ...] = ()
    coverage: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    model_split: bool = False
    dropped_records: int = 0
    notes: tuple[str, ...] = field(default=())
