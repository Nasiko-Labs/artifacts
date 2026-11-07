"""Pure: the word lists the guardrails run on. English only and incomplete by nature: a safety net, not proof
of fairness. Every list is a constant so an HR/legal review can change it in one place."""

from __future__ import annotations

import re


def _words(*phrases: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(phrases) + r")\b", re.IGNORECASE)


# Generic praise or criticism: a statement using one is repaired once, else dropped.
FILLER = (
    "hard[- ]worker", "hard[- ]working", "team player", "great attitude", "go[- ]getter", "self[- ]starter",
    "good communicator", "strong performer", "exceeds expectations", "meets expectations", "rock ?star",
    "valuable member", "asset to the team", "always", "never", "a pleasure to work with",
)
FILLER_RE = _words(*FILLER)
CONSISTENTLY_RE = _words("consistently")

# Personality words that need a quoted behavior in the same statement; adjectives for the work are fine.
TRAIT_WORDS = (
    "abrasive", "bossy", "aggressive", "emotional", "shrill", "pushy", "difficult", "nice", "sweet", "bubbly",
    "quiet", "shy", "passionate", "energetic", "young", "old", "mature", "fit in", "culture fit", "not a leader",
    "helpful", "supportive",
)
TRAIT_RE = _words(*(w.replace(" ", r"\s+") for w in TRAIT_WORDS))
WORK_NOUNS = re.compile(r"^\s+(?:timeline|schedule|plan|target|goal|deadline|roadmap|estimate|design|approach|"
                        r"refactor|rollout|scope|migration|change|fix|test|tests|caching|cache|code|system)\b",
                        re.IGNORECASE)
BEHAVIOR_FOLLOWS = re.compile(r"^\s+(?:by|when|in|during)\b", re.IGNORECASE)
ALONE_OK_WITH_BEHAVIOR = {"helpful", "supportive"}

# Ratings, scores and promotion or pay advice: never in the draft.
RATING_RE = re.compile(
    r"\b(?:exceed(?:s|ed|ing)? expectations|meets expectations|met expectations|below expectations|"
    r"does not meet|exceeds|outstanding performer|(?:high|top|low|strong|under)[- ]?perform(?:er|ers|ing|ance)?|"
    r"underperform\w*|rating|rated|promot\w+|(?:pay|salary) (?:raise|increase|rise)|bonus|compensation|"
    r"\d(?:\.\d)?\s*/\s*(?:5|10)|\d(?:\.\d)? out of (?:5|five|10|ten))\b"
    r"|\b\d(?:\.\d)?\s*/\s*(?:5|10)\b",
    re.IGNORECASE)

PRONOUNS = {
    "he": {"he", "him", "his", "himself"},
    "she": {"she", "her", "hers", "herself"},
    "they": {"they", "them", "their", "theirs", "themselves", "themself"},
}
PRONOUN_RE = re.compile(r"\b(?:" + "|".join(sorted({w for ws in PRONOUNS.values() for w in ws})) + r")\b",
                        re.IGNORECASE)

QUOTE_RE = re.compile(r"\"([^\"]{3,300})\"|“([^”]{3,300})”|(?<![A-Za-z])'([^']{8,300})'(?![A-Za-z])")


def allowed_pronouns(declared: str) -> set[str]:
    """The pronoun words a `Pronouns: she/her` line allows; none when the line is absent."""
    words = set(re.findall(r"[a-z]+", (declared or "").casefold()))
    allowed: set[str] = set()
    for key, forms in PRONOUNS.items():
        if key in words or words & forms:
            allowed |= forms
    return allowed


def without_quotes(text: str) -> str:
    """The statement with quoted spans removed: quoted words are the evidence's own, not the draft's."""
    return QUOTE_RE.sub(" ", text or "")


def quotes(text: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in QUOTE_RE.finditer(text or "")]


def trait_hits(text: str) -> list[str]:
    """Personality words used as a trait: not followed by a work noun, and (for helpful/supportive) not by
    the behavior that earned them."""
    hits: list[str] = []
    plain = without_quotes(text)
    for m in TRAIT_RE.finditer(plain):
        after = plain[m.end():]
        word = " ".join(m.group(0).casefold().split())
        if WORK_NOUNS.match(after):
            continue
        if word in ALONE_OK_WITH_BEHAVIOR and BEHAVIOR_FOLLOWS.match(after):
            continue
        hits.append(word)
    return hits
