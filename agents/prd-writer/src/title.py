"""Pure: the PRD title names the feature: the author's short feature name when it is grounded and is not a
constraint, else the need the brief states ("want to invite up to 50 teammates ..."), else a problem fact that
is not a constraint. Never a constraint, deadline or metric fact ("Invites should expire after 7 days")."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from grounding import normalise, ungrounded
from prd import Fact

MAX_TITLE_WORDS = 6
MAX_TITLE_CHARS = 60
MAX_NEED_WORDS = 10
STEM_CHARS = 5
UNTITLED = "Untitled"
_LIMIT_KINDS = frozenset({"constraint", "deadline", "metric", "number", "exclusion"})
_NEED_KINDS = ("problem", "goal", "user", "solution_idea")
_NEED = re.compile(r"\b(?:wants?|needs?|would like|(?:keep )?ask(?:s|ing)?(?: us)?|asked(?: us)?)\s+(?:to\s+)?"
                   r"([^.;:\n]+)", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


def feature_title(data: Mapping, facts: Sequence[Fact], corpus: str) -> str:
    named = _model_title(data.get("title") if isinstance(data, Mapping) else None, facts, corpus)
    return named or _need_title(facts, corpus) or _problem_title(facts) or UNTITLED


def _model_title(raw: object, facts: Sequence[Fact], corpus: str) -> str:
    title = " ".join(str(raw or "").split()).strip(" .:;-")
    if not title or len(title) > MAX_TITLE_CHARS or len(title.split()) > MAX_TITLE_WORDS:
        return ""
    if ungrounded(title, normalise(corpus)) or _limit_like(title, facts):
        return ""
    return title


def _need_title(facts: Sequence[Fact], corpus: str) -> str:
    sources = [f.snippet for kind in _NEED_KINDS for f in facts if f.kind == kind]
    for text in (*sources, corpus):
        need = _NEED.search(text or "")
        if need:
            words = need.group(1).split()[:MAX_NEED_WORDS]
            phrase = " ".join(words).rstrip(",")
            if phrase and not _limit_like(phrase, facts):
                return phrase[0].upper() + phrase[1:]
    return ""


def _problem_title(facts: Sequence[Fact]) -> str:
    for fact in facts:
        if fact.kind == "problem" and not _limit_like(fact.text, facts):
            return " ".join(fact.text.split()[:MAX_TITLE_WORDS]).rstrip(".,;:")
    return ""


def _limit_like(text: str, facts: Sequence[Fact]) -> bool:
    """Every word of `text` (by stem) is in one constraint/deadline/metric fact: it names a limit, not the feature."""
    words = _stems(text)
    return bool(words) and any(words <= _stems(f"{f.text} {f.snippet}") for f in facts if f.kind in _LIMIT_KINDS)


def _stems(text: str) -> set[str]:
    return {w[:STEM_CHARS] for w in _WORD.findall(normalise(text)) if len(w) > 2 or w.isdigit()}
