"""Pure: reading one user message. History prefix, tone sample, request vs pasted context, caps, and
the code-level cues (profile scope, length, "what did you use?", "redo")."""

from __future__ import annotations

import re
from dataclasses import dataclass

import history
from history import Turn

MAX_CONTEXT_CHARS = 8_000
MAX_SAMPLE_CHARS = 3_000

# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. This agent keeps
# no state: the history is parsed into turns (history.py) and the current draft and tone are recovered
# from them (recall.py).
_SAMPLE_CUE = re.compile(
    r"(?:here(?:'s| is) (?:how i write|a sample of (?:how i write|my writing)|an example of (?:how i write|my "
    r"writing))|match (?:this|my) (?:writing )?(?:style|tone)|(?:a )?sample of my writing|write like this"
    r"|this is how i write)\s*[:\-]?",
    re.IGNORECASE,
)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SCOPE = re.compile(r"\b(?:always|from now on|in general|by default|going forward|all my messages)\b", re.IGNORECASE)
_SHORTER = re.compile(r"\b(?:short(?:er)?|brief(?:er)?|concise|tighter|trim it)\b", re.IGNORECASE)
_LONGER = re.compile(r"\b(?:long(?:er)?|more detail(?:ed)?|expand(?: it)?)\b", re.IGNORECASE)
_FACTS_QUESTION = re.compile(
    r"^\s*(?:what|which) (?:facts |info(?:rmation)? |details )?(?:did you use|have you used|did you rely on)\b"
    r"|^\s*(?:show|list) (?:me )?(?:the )?facts(?: you used)?\b",
    re.IGNORECASE,
)
_REDO = re.compile(r"^\s*(?:redo|re-?do it|redo (?:it|the draft|this)|apply it|apply (?:it|the tone) to the "
                   r"(?:current )?draft)\s*[.!]?\s*$", re.IGNORECASE)
_EDIT_START = re.compile(r"^\s*(?:please\s+)?(?:make|change|add|remove|drop|turn|mention|say|use|fill|replace|"
                         r"rewrite|reword|shorten|cut|tweak|fix|swap|soften|redo)\b", re.IGNORECASE)
MAX_EDIT_WORDS = 15
_FROM_LINE = re.compile(r"^\s*from:\s*(.{1,120})$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Request:
    """One drafting request as the graph receives it."""

    text: str                                         # the user's message, history prefix removed
    org: tuple[str, ...] = ()                         # organization instructions (later text parts)
    history: tuple[Turn, ...] = ()                    # the platform's history slice: the only memory


@dataclass(frozen=True)
class Split:
    request: str        # what the user asked, plus any pasted context
    sample: str         # a pasted sample of the user's writing ("" if none)
    sample_truncated: bool = False


def read_first_part(raw: str) -> tuple[str, tuple[Turn, ...]]:
    """(this message, the earlier turns) from the first text part."""
    block, current = history.split(raw or "")
    return current, history.parse(block)


def split_sample(text: str) -> Split:
    """Everything after a sample cue ("here's how I write:") is the sample; the text before it is the
    request. The sample is capped; it is used for the tone profile only and never stored."""
    match = _SAMPLE_CUE.search(text or "")
    if match is None:
        return Split(request=(text or "").strip(), sample="")
    sample = text[match.end():].strip()
    return Split(request=text[:match.start()].strip(), sample=sample[:MAX_SAMPLE_CHARS],
                 sample_truncated=len(sample) > MAX_SAMPLE_CHARS)


def request_and_context(text: str) -> tuple[str, str]:
    """The first paragraph is the request; anything after a blank line is pasted context (data)."""
    parts = _PARAGRAPH_BREAK.split((text or "").strip(), maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def addressee_lines(context: str) -> tuple[str, ...]:
    """The senders of a pasted message ("From: Priya <priya@...>"): replying to them is legitimate."""
    return tuple(m.group(1).strip() for m in _FROM_LINE.finditer(context or ""))


def too_long(text: str) -> bool:
    return len(text or "") > MAX_CONTEXT_CHARS


def is_profile_scope(text: str) -> bool:
    return bool(_SCOPE.search(text or ""))


def length_hint(text: str) -> str | None:
    if _SHORTER.search(text or ""):
        return "short"
    if _LONGER.search(text or ""):
        return "long"
    return None


def asks_for_facts(text: str) -> bool:
    return bool(_FACTS_QUESTION.search(text or ""))


def is_redo(text: str) -> bool:
    return bool(_REDO.match(text or ""))


def has_words(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]{2,}", text or ""))


def looks_like_revision(text: str) -> bool:
    """A short message that edits an existing draft ("shorter", "make it a chat message", "redo")."""
    words = len((text or "").split())
    return is_redo(text) or (words <= MAX_EDIT_WORDS and (length_hint(text) is not None
                                                          or bool(_EDIT_START.match(text or ""))))
