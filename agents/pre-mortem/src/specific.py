"""Pure: the specificity guardrail, how "no generic risks" is enforced in code.

A candidate survives only if (1) its `plan_ref` is a verbatim piece (15-250 chars) of one numbered plan line,
(2) its story shares at least 2 content words with that line (generic risk words never count), (3) every
number, date, amount, email, link and name it uses is in the plan or the context, (4) its early warning and
mitigation are short, concrete and not filler (else it needs one repair), and (5) its ratings are valid.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from grounding import clean_ref, normalise, ungrounded

LEVELS = ("low", "medium", "high")
MIN_REF_CHARS, MAX_REF_CHARS = 15, 250
MIN_SHARED_WORDS = 2
MAX_FIELD_WORDS = 25
STEM_CHARS = 6
OK, REPAIR = "ok", "repair"

STOPWORDS = frozenset([
    "about", "above", "after", "again", "against", "also", "because", "been", "before", "being", "below", "between",
    "both", "could", "does", "doing", "done", "down", "during", "each", "from", "further", "have", "having", "here",
    "into", "itself", "just", "more", "most", "must", "once", "only", "other", "over", "same", "should", "some",
    "such", "than", "that", "their", "them", "then", "there", "these", "they", "this", "those", "through", "under",
    "until", "very", "were", "what", "when", "where", "which", "while", "will", "with", "within", "would", "your",
    "week", "weeks", "plan", "plans"
])
GENERIC = frozenset([
    "communication", "communicate", "scope", "creep", "resources", "resource", "stakeholders", "stakeholder",
    "alignment", "aligned", "timeline", "timelines", "budget", "budgets", "delays", "delay", "delayed", "quality",
    "risk", "risks", "team", "teams", "issues", "issue", "problems", "problem", "poor", "lack", "failure", "failed",
    "fail", "project"
])
FILLER = frozenset([
    "monitor", "monitoring", "closely", "communicate", "communication", "regularly", "careful", "keep", "an", "eye",
    "stay", "aligned", "ensure", "track", "tracking", "review", "reviews", "watch", "watching", "stay", "informed",
    "proactively", "proactive", "effectively", "manage", "manager", "better", "clear", "clearly", "closely", "more",
    "regular", "check", "checks"
])
_WORDS = re.compile(r"[a-z][a-z'-]{3,}")


@dataclass(frozen=True)
class Candidate:
    id: str
    lens: str
    title: str
    story: str
    root_assumption: str
    plan_ref: str
    likelihood: str
    impact: str
    early_warning: str
    mitigation: str
    line: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Candidate:
        return Candidate(**{k: data[k] for k in Candidate.__dataclass_fields__ if k in data})


def from_model(raw: Any, cid: str, lens: str) -> Candidate | None:
    if not isinstance(raw, dict):
        return None
    text = {k: " ".join(str(raw.get(k) or "").split()) for k in
            ("title", "story", "root_assumption", "plan_ref", "early_warning", "mitigation")}
    rating = {k: str(raw.get(k) or "").strip().casefold() for k in ("likelihood", "impact")}
    return Candidate(id=cid, lens=lens, **text, **rating)


def locate_line(plan_ref: str, lines: tuple[str, ...]) -> int | None:
    """The 1-based number of the one line that contains `plan_ref` verbatim, else None."""
    ref = clean_ref(plan_ref)
    if not MIN_REF_CHARS <= len(ref) <= MAX_REF_CHARS:
        return None
    for n, line in enumerate(lines, start=1):
        if ref in normalise(line):
            return n
    return None


def content_stems(text: str) -> set[str]:
    words = _WORDS.findall(normalise(text))
    return {w[:STEM_CHARS] for w in words if w not in STOPWORDS and w not in GENERIC}


def shares_plan_words(story: str, line: str) -> bool:
    generic = {g[:STEM_CHARS] for g in GENERIC}
    return len((content_stems(story) & content_stems(line)) - generic) >= MIN_SHARED_WORDS


def is_filler(text: str) -> bool:
    """Empty, over 25 words, or nothing but 'monitor closely'-style words."""
    words = _WORDS.findall(normalise(text))
    if not words or len(text.split()) > MAX_FIELD_WORDS:
        return True
    concrete = [w for w in words if w not in FILLER and w not in STOPWORDS and w not in GENERIC]
    return len(concrete) < MIN_SHARED_WORDS


def assess(candidate: Candidate, lines: tuple[str, ...], norm_ground: str) -> tuple[str, Candidate]:
    """("ok" | "repair" | a drop reason, the candidate with its `line` set)."""
    if candidate.likelihood not in LEVELS or candidate.impact not in LEVELS:
        return "bad rating", candidate
    if not candidate.story or not candidate.title:
        return "empty", candidate
    line = locate_line(candidate.plan_ref, lines)
    if line is None:
        return "no plan line", candidate
    located = replace(candidate, line=line)
    if not shares_plan_words(f"{candidate.title} {candidate.story}", lines[line - 1]):
        return "generic", located
    texts = (located.title, located.story, located.root_assumption, located.early_warning, located.mitigation)
    if any(ungrounded(t, norm_ground) for t in texts):
        return "ungrounded", located
    if is_filler(located.early_warning) or is_filler(located.mitigation):
        return REPAIR, located
    return OK, located


def dedup(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    kept: list[Candidate] = []
    for c in candidates:
        key = " ".join(_WORDS.findall(normalise(c.story)))
        if key not in seen:
            seen.add(key)
            kept.append(c)
    return kept
