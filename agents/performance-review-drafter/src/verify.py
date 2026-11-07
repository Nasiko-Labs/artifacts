"""Pure: the evidence-only rule and the language guardrails, run on every drafted statement.

Structural failures drop the statement (no usable evidence, a gap competency, too few items, a growth area
with no negative or mixed item). Language failures (filler, trait words, ratings, pronouns, a number, name or
quote not in the cited evidence, too long) are repaired once, then dropped."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import lexicons
from evidence import Evidence
from specific import has_metric, proper_nouns

SECTIONS = ("strengths", "growth", "summary")
MAX_WORDS = 40
CONSISTENT_MIN_MONTHS = 3
OK, REPAIR, DROP = "ok", "repair", "drop"
_TAGS = re.compile(r"\s*\[(?:\s*e\d+\s*,?)+\]\s*|\s*\((?:\s*e\d+\s*,?)+\)\s*", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*")
_NORM = re.compile(r"[^a-z0-9]+")
_CONTENT = re.compile(r"[a-z]{4,}|\d+")
_STOP = {"that", "this", "with", "from", "were", "was", "have", "has", "their", "them", "they", "into", "about",
         "after", "before", "over", "under", "which", "while", "when", "then", "than", "also", "only", "more", "most",
         "some", "each", "other", "team", "work", "worked", "showed", "made", "able", "time"}
STEM_CHARS = 5
MIN_SHARED_WORDS = 2


@dataclass(frozen=True)
class Statement:
    section: str
    competency: str
    text: str
    evidence: tuple[str, ...]
    self_reported: bool = False

    def to_dict(self) -> dict:
        return {"section": self.section, "competency": self.competency, "text": self.text,
                "evidence": list(self.evidence), "self_reported": self.self_reported}

    @staticmethod
    def from_dict(data: Mapping) -> Statement:
        return Statement(str(data["section"]), str(data.get("competency") or ""), str(data["text"]),
                         tuple(data.get("evidence") or ()), bool(data.get("self_reported")))


@dataclass(frozen=True)
class Context:
    usable: Mapping[str, Evidence]
    names: Sequence[str]
    gaps: Sequence[str]
    person: str = ""
    pronouns: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Verdict:
    kind: str
    statement: Statement | None
    reasons: tuple[str, ...] = ()


def norm(text: str) -> str:
    return " " + _NORM.sub(" ", (text or "").casefold()).strip() + " "


def from_raw(raw: Mapping) -> Statement | None:
    if not isinstance(raw, Mapping):
        return None
    text = " ".join(_TAGS.sub(" ", str(raw.get("text") or "")).split())
    ids = tuple(dict.fromkeys(str(i).strip().casefold() for i in raw.get("evidence") or () if isinstance(i, str)))
    section = str(raw.get("section") or "").casefold()
    return Statement(section, str(raw.get("competency") or "").casefold().strip(), text, ids)


def check(statement: Statement, ctx: Context) -> Verdict:
    structural = _structure(statement, ctx)
    if isinstance(structural, str):
        return Verdict(DROP, None, (structural,))
    reasons = language(structural, ctx)
    return Verdict(REPAIR, structural, tuple(reasons)) if reasons else Verdict(OK, structural)


def _structure(s: Statement, ctx: Context) -> Statement | str:
    if s.section not in SECTIONS or not s.text:
        return "not a strengths, growth or summary statement"
    cited = [ctx.usable[i] for i in s.evidence if i in ctx.usable and _about(s.text, ctx.usable[i].text, ctx.person)]
    if not cited:
        return "cites no usable evidence that it describes"
    if s.section != "summary" and (s.competency not in ctx.names or s.competency in ctx.gaps):
        return f"competency '{s.competency}' is unknown or an evidence gap"
    non_self = [c for c in cited if c.source != "self"]
    self_only = not non_self
    if s.section != "summary" and not self_only and len(cited) < 2 and not any(has_metric(c) for c in cited):
        return "needs 2 evidence items or 1 with a metric"
    if s.section == "growth" and not any(c.valence in ("negative", "mixed") for c in cited):
        return "a growth area needs a negative or mixed item"
    return replace(s, evidence=tuple(c.id for c in cited), self_reported=self_only)


def _stems(text: str, person: str) -> set[str]:
    words = _CONTENT.findall((text or "").casefold())
    return {w[:STEM_CHARS] for w in words if w not in _STOP and w != person.casefold()}


def _about(statement: str, evidence: str, person: str) -> bool:
    """A cited item must share at least 2 content words with the statement: padding a claim with unrelated ids
    does not make it evidence-backed."""
    return len(_stems(statement, person) & _stems(evidence, person)) >= MIN_SHARED_WORDS


def language(s: Statement, ctx: Context) -> list[str]:
    cited = [ctx.usable[i] for i in s.evidence]
    ground = norm(" ".join(c.text for c in cited))
    plain = lexicons.without_quotes(s.text)
    reasons: list[str] = []
    if len(s.text.split()) > MAX_WORDS:
        reasons.append(f"longer than {MAX_WORDS} words")
    reasons += [f"filler phrase '{m.group(0)}'" for m in lexicons.FILLER_RE.finditer(plain)]
    if lexicons.CONSISTENTLY_RE.search(plain) and len({c.date[:7] for c in cited if c.date}) < CONSISTENT_MIN_MONTHS:
        reasons.append("'consistently' needs 3 dated items in 3 different months")
    reasons += [f"personality word '{w}' without a quoted behavior" for w in lexicons.trait_hits(s.text)
                if not lexicons.quotes(s.text)]
    reasons += [f"rating or promotion phrase '{m.group(0)}'" for m in lexicons.RATING_RE.finditer(plain)]
    used = {m.group(0).casefold() for m in lexicons.PRONOUN_RE.finditer(plain)}
    reasons += [f"pronoun '{p}' not given by the manager" for p in sorted(used - ctx.pronouns)]
    reasons += _ungrounded(s.text, ground, ctx)
    return reasons


def _ungrounded(text: str, ground: str, ctx: Context) -> list[str]:
    reasons = [f"quote not in the cited evidence: \"{q}\"" for q in lexicons.quotes(text) if norm(q) not in ground]
    for number in _NUMBER.findall(text):
        if norm(number) not in ground:
            reasons.append(f"number '{number}' not in the cited evidence")
    ignore = {ctx.person.casefold(), *(n.casefold() for n in ctx.names)}
    for word in proper_nouns(lexicons.without_quotes(text), ignore):
        if norm(word) not in ground:
            reasons.append(f"name '{word}' not in the cited evidence")
    return reasons
