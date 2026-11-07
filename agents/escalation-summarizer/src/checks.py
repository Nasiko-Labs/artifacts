"""The code checks on what the model wrote: the guarantee, not the prompt.

(1) every number, date word, money, percentage, email, URL and capitalised name in a field must be in
the thread; (2) blame words are not allowed; (3) hedges the thread did not make are removed; (4) length
caps cut at a sentence boundary; (5) a thread whose latest message resolves the issue leads with
"Status: resolved in the thread"; (6) contradicting facts are rendered by code, never smoothed."""

from __future__ import annotations

import re
from collections.abc import Sequence

import grounding as g
from facts import Fact
from messages import Message, index_of, own

BLAME = re.compile(r"\b(?:fault|faults|faulty|blame[sd]?|blaming|negligen(?:t|ce)|incompeten(?:t|ce)|"
                   r"should have|shouldn't have|failed to|dropped the ball|screwed up|messed up)\b", re.IGNORECASE)
HEDGES = ("probably", "likely", "possibly", "presumably", "apparently", "perhaps", "seemingly")
_HEDGE = re.compile(r"\b(?:most |very |quite )?(?:" + "|".join(HEDGES) + r")\b,?\s*", re.IGNORECASE)
_CAPITALISED = re.compile(r"(?<![\w@/.])[A-Z][A-Za-z0-9&’'-]*[A-Za-z0-9]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_RESOLVED = re.compile(r"\b(?<!not )(?<!n't )(?<!un)(?:fixed|resolved|closed)\b", re.IGNORECASE)
_NEGATED_RESOLVED = re.compile(r"\b(?:not|isn't|wasn't|never|no longer|re-?opened|still)\b[^.]{0,20}\b"
                               r"(?:fixed|resolved|closed)\b|\bunresolved\b|\breopen", re.IGNORECASE)
RESOLVED_LEAD = "Status: resolved in the thread."
COMMON = frozenset(re.findall(r"\S+", """
a an the this that these those it its they them their there here he she his her we our you your i
status support engineering engineer engineers customer customers client clients user users team teams
account accounts product sales success ops operations management manager managers lead leads leadership
ask asks also no not none confirm decide decision please both latest last first next since after before until
per as on in at by for from with without of to and or but so if when while once then still yet only all
some several many one two three
it's they're we're issue issues problem problems incident ticket tickets escalation escalations thread
update updates fix fixes hotfix release patch workaround cache bug bugs outage error errors data export
exports vp ceo cto cfo coo cio head director executive exec execs board legal finance billing security
monday tuesday wednesday thursday friday saturday sunday today tomorrow yesterday week weekend month
enterprise renewal contract sla p1 p2 p3 high medium low urgent critical blocker blockers impact impacted
"""))
STARTERS = frozenset(re.findall(r"\S+", """
however despite additionally currently meanwhile although though because following according given overall
finally initially recently subsequently therefore thus moreover furthermore during each every multiple key note
now later earlier nevertheless instead otherwise previously separately specifically unfortunately another other
further whether what who where which how why new is are was were has have had will would can could should may
must do does did be been being affected unable two three four five six seven eight nine ten
"""))
_ORDINARY_SUFFIXES = ("ly", "ing", "ed", "ion", "ions", "ity", "ous", "ive", "ment", "ments", "ness", "ance", "ence")
_AFTER_BREAK = re.compile(r"[.!?:;]\s*[\"'(\[]?\s*$")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text or "") if s.strip()]


def ungrounded_names(text: str, norm_thread: str, allowed: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Capitalised words that are neither common words nor written anywhere in the thread. A word that is
    capitalised only because it opens a sentence is skipped when it reads as an ordinary English word."""
    missing = []
    for match in _CAPITALISED.finditer(text or ""):
        word = re.sub(r"['’]s$", "", match.group(0)).casefold()
        if word in COMMON or word in allowed or g.is_calendar_word(word) or (
                _opens_sentence(text, match.start()) and _ordinary(word)):
            continue
        if not re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", norm_thread):
            missing.append(match.group(0))
    return tuple(missing)


def _opens_sentence(text: str, start: int) -> bool:
    return not text[:start].strip() or bool(_AFTER_BREAK.search(text[:start]))


def _ordinary(word: str) -> bool:
    return word in STARTERS or word.endswith(_ORDINARY_SUFFIXES)


def issues(text: str, norm_thread: str, allowed: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Why a piece of model text cannot stand (empty when it can)."""
    found = [f"'{t}' is not in the thread" for t in g.ungrounded_tokens(text, norm_thread)]
    found += [f"the name '{n}' is not in the thread" for n in ungrounded_names(text, norm_thread, allowed)]
    found += [f"blame wording '{m.group(0)}'" for m in BLAME.finditer(text or "")]
    return tuple(found)


def remove_hedges(text: str, norm_thread: str) -> str:
    """Drops hedge words the thread itself never uses (a hedge the thread makes is kept)."""
    def drop(match: re.Match) -> str:
        word = match.group(0).strip(" ,").split()[-1].casefold()
        return match.group(0) if re.search(rf"\b{word}\b", norm_thread) else ""
    out = _HEDGE.sub(drop, text or "")
    return re.sub(r"\s{2,}", " ", out).strip()


def cap_words(text: str, cap: int) -> str:
    """At most `cap` words, cut at the last sentence end that fits; else at the word cap with "…"."""
    if len((text or "").split()) <= cap:
        return text or ""
    kept, count = [], 0
    for sentence in sentences(text):
        count += len(sentence.split())
        if count > cap:
            break
        kept.append(sentence)
    if kept:
        return " ".join(kept)
    return " ".join(text.split()[:cap]).rstrip(" ,;:-") + "…"


def is_resolved(messages: Sequence[Message]) -> bool:
    """The latest message says the issue is fixed / resolved / closed (and does not negate it)."""
    mine = own(tuple(messages))
    if not mine:
        return False
    body = mine[-1].body
    return bool(_RESOLVED.search(body)) and not _NEGATED_RESOLVED.search(body)


def conflicts(facts: Sequence[Fact]) -> tuple[tuple[Fact, Fact], ...]:
    """Pairs the model flagged as disagreeing, both checked facts, earlier message first."""
    by_id = {f.id: f for f in facts}
    pairs, seen = [], set()
    for fact in facts:
        other = by_id.get(fact.contradicts)
        if other is None or other.id == fact.id:
            continue
        key = frozenset((fact.id, other.id))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(tuple(sorted((fact, other), key=lambda f: (index_of(f.message) or 10**6, f.id))))
    return tuple(pairs)


def conflict_line(pair: tuple[Fact, Fact]) -> str:
    a, b = pair
    topic = {"status": "the status", "problem": "the cause"}.get(a.kind, f"the {a.kind}")
    where_a = f" ({a.message})" if a.message else ""
    where_b = f" ({b.message})" if b.message else ""
    return f'The thread disagrees about {topic}: "{a.snippet}"{where_a} vs "{b.snippet}"{where_b}.'
