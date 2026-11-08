"""Pure: what the user asked for, read in code. People they named, repositories, the window, JSON mode,
and the two requests declined before any call: ranking people, and messaging anyone.

A name is taken only from the user's own words: an @handle, a possessive ("Priya's"), or capitalised
words after "for/to/about/of" ("kudos for Priya and Sam"). Names the model reports are kept only when
their words appear in the message (`named_in_message`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from window import Window, parse_window

MAX_NAMED = 8
MAX_LABEL_CHARS = 60
_NAME = r"(?:@[A-Za-z0-9][\w.-]*|[A-Z][\w.-]*(?:\s+[A-Z][\w-]*)?(?:'s|’s)?)"
_LIST = re.compile(rf"\b(?:for|to|about|of|on)\s+({_NAME}(?:\s*(?:,|\band\b|&)\s*{_NAME})*)")
_POSSESSIVE = re.compile(r"\b([A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)(?:'s|’s)\b")
_HANDLE = re.compile(r"(?<![\w@])@([A-Za-z0-9][\w.-]{0,38})")
_SPLIT = re.compile(r"\s*(?:,|\band\b|&)\s*")
_STOP = frozenset({
    "the", "this", "that", "last", "since", "my", "our", "team", "repo", "kudos", "evidence", "collect", "just",
    "only", "show", "please", "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec", "monday", "tuesday", "wednesday", "thursday", "friday", "q1", "q2", "q3", "q4",
    "h1", "h2", "pr", "prs", "i", "me", "everyone", "all", "half", "quarter", "year", "promo", "promotion",
    "review", "wins", "praise", "who", "what", "it", "them", "him", "her", "you", "us",
})
_REPO_URL = re.compile(r"https?://[^/\s]+/([\w.-]+/[\w.-]+?)(?:\.git)?(?=[/\s?#)]|$)")
_REPO_SLASH = re.compile(r"(?<![\w/:.])([A-Za-z][\w.-]*/[A-Za-z][\w.-]*)(?![\w/])")
_REPO_WORD = (re.compile(r"\b(?:in|from|on|for)\s+(?:the\s+)?([\w.-]+)\s+(?:repo|repository)\b", re.IGNORECASE),
              re.compile(r"\b(?:repo|repository)\s+([\w./-]+)", re.IGNORECASE))
_RANKING = re.compile(
    r"\brank\w*|\bleader ?board\b|\bcompare\b|\bcomparison\b|\bscor(?:e|es|ed|ing)\b|\bbest\b|\bworst\b"
    r"|\btop\s*\d*\s*(?:contributor|performer|engineer|developer|person|people)s?\b"
    r"|\bwho\s+(?:\w+\s+){0,2}(?:the\s+)?(?:most|least)\b|\bmost\s+(?:valuable|productive|impactful)\b",
    re.IGNORECASE)
_SEND = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:send|post|message|dm|email|tell|ping|notify|thank|publish)\b"
    r"|\bthank\s+(?:\w+\s+){1,2}for\s+me\b|\b(?:send|post|share)\b.{0,40}\b(?:thank|thanks|kudos|"
    r"shout\s*-?outs?)\b|\b(?:post|share)\b.{0,30}\bin\s+#",
    re.IGNORECASE)
_JSON = re.compile(r"\b(?:as\s+json|json\s+only|in\s+json)\b", re.IGNORECASE)
_UNSAFE = re.compile(r"[\[\]()<>*_`|#\\\x00-\x1f]")
_SPACES = re.compile(r"\s+")
_TOKENS = re.compile(r"[^0-9a-z]+")


@dataclass(frozen=True)
class Request:
    people: tuple[str, ...]
    people_left_out: int
    repos: tuple[str, ...]
    window: Window
    as_json: bool


def parse(text: str, today: date) -> Request:
    first = (text or "").split("\n", 1)[0]
    names = named_people(first)
    return Request(people=names[:MAX_NAMED], people_left_out=max(0, len(names) - MAX_NAMED),
                   repos=repos(first), window=parse_window(first, today), as_json=wants_json(text))


_IN_SCOPE_WORDS = re.compile(r"\b(?:kudos|praise|wins?|evidence|promo(?:tion)?|pull requests?|prs?|shipped|"
                             r"contributions?|accomplishments?|achievements?|review cycle|shout ?outs?)\b",
                             re.IGNORECASE)


def looks_in_scope(text: str) -> bool:
    """A kudos-shaped request (names someone or a repository, or uses the vocabulary): a model saying
    off-topic for it is not trusted."""
    first = (text or "").split("\n", 1)[0]
    return bool(named_people(first) or repos(first) or _IN_SCOPE_WORDS.search(first))


def asks_for_ranking(text: str) -> bool:
    return bool(_RANKING.search((text or "").split("\n", 1)[0]))


def asks_to_send(text: str) -> bool:
    return bool(_SEND.search((text or "").split("\n", 1)[0]))


def wants_json(text: str) -> bool:
    return bool(_JSON.search(text or ""))


def clean_label(text: str) -> str:
    return _SPACES.sub(" ", _UNSAFE.sub(" ", text or "")).strip()[:MAX_LABEL_CHARS]


def tokens(text: str) -> tuple[str, ...]:
    return tuple(t for t in _TOKENS.split((text or "").casefold()) if t)


def named_people(text: str) -> tuple[str, ...]:
    found: list[str] = [f"@{h}" for h in _HANDLE.findall(text)]
    for m in _LIST.finditer(text):
        found.extend(_SPLIT.split(m.group(1)))
    found.extend(_POSSESSIVE.findall(text))
    out: list[str] = []
    for raw in found:
        name = _strip_possessive(raw).strip()
        words = [w for w in name.split() if w.casefold().lstrip("@") not in _STOP]
        label = clean_label(" ".join(words))
        if label and label.casefold() not in {o.casefold() for o in out}:
            out.append(label)
    return tuple(out)


def named_in_message(names: Iterable[object], message: str) -> tuple[str, ...]:
    """Names the model says the user named, kept only when their words appear in the message."""
    text = f" {' '.join(tokens(message))} "
    kept: list[str] = []
    for raw in names:
        if not isinstance(raw, str):
            continue
        words, label = tokens(raw), clean_label(raw)
        if words and label and f" {' '.join(words)} " in text and label not in kept and words[0] not in _STOP:
            kept.append(label)
    return tuple(kept[:MAX_NAMED])


def repos(text: str) -> tuple[str, ...]:
    found = [*_REPO_URL.findall(text), *_REPO_SLASH.findall(text)]
    for pattern in _REPO_WORD:
        found.extend(pattern.findall(text))
    return tuple(dict.fromkeys(r.strip("./") for r in found if r.strip("./") and r.casefold() not in _STOP))


def from_envelope(text: str) -> str:
    """A previous run's envelope (JSON, maybe fenced) reads as a request for the same people; anything
    else is returned unchanged. Fields read: `per_person[].person`, else `summary`."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    if not raw.startswith("{"):
        return text
    try:
        data = json.loads(raw)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text
    people = [p.get("person") for p in data.get("per_person") or [] if isinstance(p, dict)]
    names = [clean_label(p) for p in people if isinstance(p, str) and clean_label(p)]
    if names:
        return "Kudos for " + ", ".join(names[:MAX_NAMED])
    summary = data.get("summary")
    return summary.strip() if isinstance(summary, str) and summary.strip() else text


def _strip_possessive(name: str) -> str:
    return re.sub(r"(?:'s|’s)$", "", name)
