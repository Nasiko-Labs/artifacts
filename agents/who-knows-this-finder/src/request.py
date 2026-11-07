"""Pure: what the user's message asks for, parsed in code (no LLM call): the topic, scope hints
("in the payments repo", "#infra"), "exclude me", JSON output, an envelope as input, the follow-ups
that need no tool call (MORE, WHY <name>), and requests to rank people (always declined)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_JSON_WORDS = re.compile(r"\b(?:as json|json only|in json|json output)\b", re.IGNORECASE)
_REPO = re.compile(r"\b(?:in|from|within|inside)\s+(?:the\s+|our\s+)?(?:repo(?:sitory)?\s+)?"
                   r"(?P<a>[\w./-]+)\s+(?:repo|repository)\b|\brepo(?:sitory)?[:\s]+(?P<b>[\w./-]+/[\w.-]+|[\w.-]+)",
                   re.IGNORECASE)
_CHANNEL = re.compile(r"(?<![\w&])#(?P<c>[a-z0-9][\w.-]{0,79})", re.IGNORECASE)
_EXCLUDE_ME = re.compile(r"\b(?:exclude|excluding|except|not|without|leave out|other than)\s+(?:me|myself)\b",
                         re.IGNORECASE)
_MORE = re.compile(r"(?:and\s+)?(?:who\s+else|any(?:one|body)\s+else|(?:show\s+)?more(?:\s+people)?|next(?:\s+ones?)?|"
                   r"others?|the\s+rest)(?:\s+knows?(?:\s+it)?)?", re.IGNORECASE)
_WHY = re.compile(r"why\s+(?:is\s+it\s+|did\s+you\s+(?:pick|choose|name)\s+|is\s+)?(?P<who>#?[\w.' -]{1,60}?)",
                  re.IGNORECASE)
_PRONOUNS = frozenset({"him", "her", "them", "they", "he", "she", "that", "this", "it"})
_RANK_WORDS = re.compile(r"\b(?:best|worst|smartest|strongest|weakest|most\s+(?:senior|productive|talented|skilled|"
                         r"valuable)|least\s+\w+|rank(?:ing)?|rate|grade|top\s+performers?|underperform\w*|"
                         r"promot\w*|fire|firing|lay\s*off|layoffs?|salar\w*|seniority|lazy)\b",
                         re.IGNORECASE)
_PERSON_WORDS = re.compile(r"\b(?:engineers?|developers?|devs?|people|person|employees?|members?|teammates?|"
                           r"coders?|programmers?|staff|colleagues?|managers?|hires?|"
                           r"performers?)\b",
                           re.IGNORECASE)
_PUNCT_END = re.compile(r"[\s?.!]+$")
# A request plainly about who knows something: never declined on the model's word alone.
_WHO_KNOWS = re.compile(r"\bwho(?:'s|\s+is|\s+are|\s+else)?\s+(?:knows?|owns?|understands?|maintains?|wrote|built|"
                        r"worked\s+on|works\s+on|has\s+(?:context|experience)|can\s+(?:help|explain)|"
                        r"should\s+i\s+(?:ask|talk\s+to|ping)|(?:is\s+)?(?:the\s+)?(?:expert|owner|go-to))\b|"
                        r"\b(?:expert|owner)s?\s+(?:on|of|for|in)\b|\bwho\s+to\s+(?:ask|talk\s+to)\b",
                        re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    topic: str
    repo: str = ""
    channel: str = ""
    exclude_me: bool = False
    as_json: bool = False


def parse_request(text: str) -> Request:
    raw = topic_from_envelope(_JSON_WORDS.sub("", text or "")) or (text or "").strip()
    repo = _repo(raw)
    channel_match = _CHANNEL.search(raw)
    return Request(topic=_JSON_WORDS.sub("", raw).strip(), repo=repo,
                   channel=channel_match.group("c") if channel_match else "",
                   exclude_me=bool(_EXCLUDE_ME.search(raw)), as_json=wants_json(text))


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def topic_from_envelope(text: str) -> str:
    """A previous step's envelope (JSON) as input: its `topic` field."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    if not stripped.startswith("{"):
        return ""
    try:
        data = json.loads(stripped)
    except ValueError:
        return ""
    topic = data.get("topic") if isinstance(data, dict) else None
    return topic.strip() if isinstance(topic, str) else ""


def _repo(text: str) -> str:
    match = _REPO.search(text)
    if not match:
        return ""
    name = (match.group("a") or match.group("b") or "").strip("./")
    return "" if name.casefold() in {"the", "our", "a", "this", "that"} else name


def is_more(text: str) -> bool:
    return bool(_MORE.fullmatch(_PUNCT_END.sub("", (text or "").strip())))


def why_whom(text: str) -> str | None:
    """"Why Tom?" → "Tom"; "why #2" → "#2"; None when the message is not a why-question about a person."""
    match = _WHY.fullmatch(_PUNCT_END.sub("", (text or "").strip()))
    if not match:
        return None
    who = match.group("who").strip()
    return None if who.casefold() in _PRONOUNS or len(who.split()) > 4 else who


def asks_who_knows(text: str) -> bool:
    return bool(_WHO_KNOWS.search(text or ""))


def ranks_people(text: str) -> bool:
    """"Who is the best engineer here?": a judgement of people, not of evidence on a topic."""
    return bool(_RANK_WORDS.search(text or "")) and bool(_PERSON_WORDS.search(text or ""))
