"""Pure: reading one user message. History prefix (split off and parsed by `history.py`), voice sample, platform and goal words, and the
code-level cues (json mode, "show the facts", "post it for me", a private message, a revision)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from history import Turn

MAX_SOURCE_CHARS = 8_000
MAX_SAMPLE_CHARS = 3_000
GOALS = ("announce", "educate", "opinion", "story", "hire", "event")


@dataclass(frozen=True)
class Request:
    """One drafting request as the flow receives it. Built fresh from each message: nothing carries over."""

    text: str                      # the user's message, history prefix removed
    org: tuple[str, ...] = ()      # organization instructions (later text parts): rules, never facts
    goal: str | None = None        # set when the message answers the agent's question with a kind of post
    asked: bool = False            # this message answers the agent's question (so it is not asked again)
    history: tuple[Turn, ...] = ()  # the platform's earlier turns, oldest first: the only memory


@dataclass(frozen=True)
class Split:
    request: str        # what the user asked, plus any pasted material
    sample: str         # a pasted sample of the user's writing ("" if none)
    sample_truncated: bool = False


_SAMPLE_CUE = re.compile(
    r"(?:here(?:'s| is) (?:how i write|a (?:past|previous|recent|old) post(?: of mine)?|my voice|a sample of my "
    r"(?:writing|posts?))|(?:my )?voice sample|match (?:this|my) (?:writing )?(?:style|voice|tone)|write like this"
    r"|this is how i write|a past post of mine|sample of my writing)\s*[:\-]?",
    re.IGNORECASE,
)
_SAMPLE_END = re.compile(r"\n\s*(?:now |then |and )?(?:please )?(?:draft|write|create|make|turn|do)\b", re.IGNORECASE)
_PLATFORM = (
    ("both", r"\bboth\b|\blinked ?in and (?:x|twitter)\b|\b(?:x|twitter) and linked ?in\b"),
    ("x_thread", r"\bthread\b|🧵"),
    ("x", r"\btweets?\b|\btwitter\b|\b(?:on|for|to) x\b|\ban? x post\b|\bx post\b|\bx\b(?= version)"),
    ("linkedin", r"\blinked ?in\b"),
)
_GOAL_WORDS = (
    ("hire", r"\bhiring\b|\bwe'?re hiring\b|\bopen roles?\b|\bjoin (?:our|the) team\b|\bjob (?:post|opening)\b"),
    ("event", r"\bwebinar\b|\bmeetup\b|\bconference\b|\bevent\b|\bworkshop\b|\bjoin us (?:on|at)\b"),
    ("announce", r"\blaunch(?:ed|ing)?\b|\bannounc\w*|\breleas\w*|\bshipped\b|\bintroduc\w*|\bnow available\b"),
    ("story", r"\bstory\b|\bjourney\b|\byears ago\b|\bwhen i (?:started|was)\b"),
    ("opinion", r"\bopinion\b|\bhot take\b|\bmy take\b|\bunpopular\b|\bcontrarian\b|\bi (?:think|believe)\b"),
    ("educate", r"\btips?\b|\blessons?\b|\bhow to\b|\bguide\b|\bmistakes?\b|\bchecklist\b"),
)
_JSON = re.compile(r"\b(?:as json|json only|in json|json format)\b", re.IGNORECASE)
_FACTS = re.compile(r"^\s*(?:show|list|what are) (?:me )?(?:the )?facts(?: (?:you )?used)?\b"
                    r"|^\s*(?:what|which) facts did you use\b", re.IGNORECASE)
_PUBLISH = re.compile(
    r"^\s*(?:(?:can|could|would) you |please |now |go ahead and |just )*(?:post|publish|schedule|share|tweet|send)"
    r"\s+(?:it|this|that|them|these|(?:variant|option|number|#)\s*\d|the (?:post|thread|draft|tweet|first|second|"
    r"third|one)s?)\b[^\n]{0,60}$", re.IGNORECASE)
_PRIVATE = re.compile(r"\b(?:e-?mails?|dms?|direct message|text message|slack message|private message|inmail)\b"
                      r"[^\n]{0,40}\bto\b|\b(?:e-?mail|message|dm|text) (?:to )?(?:my|our|the) (?:boss|manager|team|"
                      r"customers?|client|colleague|recruiter)\b", re.IGNORECASE)
_ARITHMETIC = re.compile(r"^\s*(?:what(?:'s| is)|calculate|compute)\s+[\d\s+\-*/x×().^%]+\??\s*$", re.IGNORECASE)
_REQUEST_FILLER = re.compile(
    r"\b(?:please|can|could|would|you|draft|write|create|make|generate|give|me|us|a|an|the|some|one|two|three|"
    r"few|[0-9]|linked ?in|x|twitter|tweets?|threads?|posts?|social|media|variants?|versions?|options?|ideas?|for|"
    r"on|my|our|new|both|and|it|i|need|want|single|short|long|announcing|announcement|about|with|of)\b"
    r"|[\s,.;:!?'\"-]+", re.IGNORECASE)
_NEW_SET = re.compile(r"\b(?:draft|write|create|generate|turn|new)\b[^\n]{0,60}\b(?:post|tweet|thread|ideas?)\b"
                      r"|\b(?:post|tweet|thread) (?:about|announcing|on)\b"
                      r"|\b(?:draft|write|create|generate) (?:one|another|something|a new one)\b", re.IGNORECASE)
_REDO = re.compile(r"^\s*(?:redo|re-?do it|redo (?:it|them|the variants)|apply (?:it|my voice)(?: to (?:it|them))?)"
                   r"\s*[.!]?\s*$", re.IGNORECASE)
_VARIANT_REF = re.compile(r"\b(?:variant|version|option|draft|number|post|#)\s*([1-9])\b|\bmake ([1-9])\b"
                          r"|\bthe (first|second|third|fourth|fifth|sixth) (?:one|variant|version|option)\b",
                          re.IGNORECASE)
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
REVISION_MAX_CHARS = 400


def split_sample(text: str) -> Split:
    """The text after a sample cue ("here's a past post:") up to a line that starts a request ("draft one
    about ...") is the sample; the rest is the request. The sample is capped and never stored."""
    match = _SAMPLE_CUE.search(text or "")
    if match is None:
        return Split(request=(text or "").strip(), sample="")
    after = text[match.end():]
    end = _SAMPLE_END.search(after)
    sample = (after[:end.start()] if end else after).strip()
    rest = after[end.start():] if end else ""
    request = f"{text[:match.start()].strip()}\n{rest.strip()}".strip()
    return Split(request=request, sample=sample[:MAX_SAMPLE_CHARS], sample_truncated=len(sample) > MAX_SAMPLE_CHARS)


def detect_platform(text: str) -> str | None:
    lowered = (text or "").casefold()
    if re.search(_PLATFORM[0][1], lowered):
        return "both"
    has_x = any(re.search(pattern, lowered) for name, pattern in _PLATFORM[1:3])
    if has_x and re.search(_PLATFORM[3][1], lowered):
        return "both"
    return next((name for name, pattern in _PLATFORM[1:] if re.search(pattern, lowered)), None)


def detect_goal(text: str) -> str | None:
    return next((goal for goal, pattern in _GOAL_WORDS if re.search(pattern, text or "", re.IGNORECASE)), None)


def wants_json(text: str) -> bool:
    return bool(_JSON.search(text or ""))


def asks_for_facts(text: str) -> bool:
    return bool(_FACTS.search(text or ""))


def asks_to_publish(text: str) -> bool:
    return bool(_PUBLISH.match((text or "").strip()))


def is_private_message(text: str) -> bool:
    return bool(_PRIVATE.search(text or "")) and not re.search(r"\b(?:linked ?in|tweet|thread|x) post\b", text or "",
                                                                re.IGNORECASE)


def is_arithmetic(text: str) -> bool:
    return bool(_ARITHMETIC.match(text or ""))


def has_material(text: str) -> bool:
    """False for a bare "draft a LinkedIn post": nothing is left once request words are removed."""
    residual = _REQUEST_FILLER.sub(" ", text or "")
    return len(re.findall(r"[A-Za-z]{2,}", residual)) >= 2


def is_revision(text: str, has_set: bool) -> bool:
    """A short instruction about the current set ("punchier hook", "make 2 a thread"), not new material."""
    plain = (text or "").strip()
    return has_set and len(plain) <= REVISION_MAX_CHARS and not _NEW_SET.search(plain)


def variant_refs(text: str) -> tuple[int, ...]:
    found = []
    for m in _VARIANT_REF.finditer(text or ""):
        found.append(int(m.group(1) or m.group(2)) if (m.group(1) or m.group(2)) else _ORDINALS[m.group(3).casefold()])
    return tuple(dict.fromkeys(found))


def is_redo(text: str) -> bool:
    return bool(_REDO.match(text or ""))
