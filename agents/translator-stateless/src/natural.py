"""Pure: a translation asked for in natural words, read in code before any LLM call, plus the code check on
the scope call's extraction.

    "how do you say thank you in German", "what's 'good morning' in French?", "translate I am hungry into
    spanish", "i am hungry in spanish", "Spanish for where is the train station?", "what does 'gracias' mean"

`parse` returns (instruction, text): the instruction is only the language part ("in formal Spanish"), so a
language named inside the text is never read as the target. It is conservative: the language must be a known
one (except after an explicit "translate ... into/to"), and a "text" made only of filler ("now", "it", "this")
is never taken, so "now in French" stays a follow-up. `checked_extraction` keeps only what the model copied
from the message; anything else falls back to the whole message."""

from __future__ import annotations

import re
from dataclasses import dataclass

import languages
import request

NATURAL_MAX_CHARS = 600
BARE_MAX_WORDS = 20
TAIL_MAX_WORDS = 6
MEANING_TARGET = "into English"

_Q = "\"'“”‘’«»"
_GREETING = re.compile(r"^(?:hey|hi|hello|ok|okay)\b[,!.]?\s*", re.IGNORECASE)
_POLITE = re.compile(r"^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?", re.IGNORECASE)
_TRAIL = re.compile(r"(?:[\s,]*\bplease)?[\s?!.]*$", re.IGNORECASE)
_VERB = (r"tell me how (?:to|do you|would you|i) (?:say|write)|how (?:do|would|can|should|could|will) "
         r"(?:you|i|we|one) (?:say|write|translate)|how to (?:say|write|translate)|how is|how's|what is|"
         r"what's|whats|say|translate|tell me|give me|write|put|turn|convert|change|render")
_TAIL = r"(?P<tail>(?:in|into|to)\s+[^\n]{1,60})"
_WITH_VERB = re.compile(rf"^(?P<verb>{_VERB})\s+(?P<text>.+)\s+{_TAIL}$", re.IGNORECASE)
_BARE = re.compile(r"^(?P<text>.+)\s+(?P<tail>(?:in|into)\s+[^\n]{1,60})$", re.IGNORECASE)
_LANG_FOR = re.compile(r"^(?P<what>(?:what is|what's|whats)\s+)?(?:the\s+)?(?P<lang>[^\n]{1,40}?)\s+"
                       r"(?:(?:word|phrase|term|expression)\s+)?for\s+(?P<text>.+)$", re.IGNORECASE)
_MEANING = re.compile(r"^what does\s+(?P<text>.+?)\s+mean(?:\s+(?P<tail>in\s+[^\n]{1,60}))?$", re.IGNORECASE)
_WORD_FOR = re.compile(r"^(?:the\s+)?(?:word|phrase|term|expression)\s+for\s+", re.IGNORECASE)
_SETTING_WORDS = frozenset({
    "in", "into", "to", "please", "a", "the", "language", "and", "but", "with", "using", "formal", "formally",
    "casual", "casually", "informal", "informally", "polite", "politely", "neutral", "friendly", "business",
    "professional", "relaxed", "colloquial", "tone", "register", "word", "phrase", "term", "expression"})
_FILLER = frozenset({
    "now", "again", "please", "it", "this", "that", "these", "those", "the", "a", "same", "text", "one", "too",
    "also", "and", "then", "me", "all", "everything", "above", "following", "instead", "more", "less", "formal",
    "casual", "here", "something", "anything", "them", "him", "her", "us", "my", "your", "message", "email"})
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?")


@dataclass(frozen=True)
class Extraction:
    """What the scope call says: `off_topic`, or a text to translate with an optional target name."""

    status: str                     # translate | off_topic
    text: str = ""
    target: str = ""


def parse(message: str) -> tuple[str, str] | None:
    """(instruction, text) for a one-line natural request, or None."""
    line = (message or "").strip()
    if not line or "\n" in line or len(line) > NATURAL_MAX_CHARS:
        return None
    polite = _POLITE.sub("", line, count=1).strip()
    greeted = _POLITE.sub("", _GREETING.sub("", line, count=1), count=1).strip()
    for core in dict.fromkeys((greeted, polite)):  # "hey, how do you say ..." but also "hello in Spanish"
        found = _with_verb(core) or _meaning(core) or _lang_for(core) or _bare(core)
        if found:
            return found
    return None


def _with_verb(core: str) -> tuple[str, str] | None:
    found = _WITH_VERB.match(_trimmed(core))
    if not found:
        return None
    text = _WORD_FOR.sub("", found.group("text"), count=1)
    allow_unknown = found.group("verb").casefold() == "translate"
    return _result(found.group("tail"), text, allow_unknown)


def _meaning(core: str) -> tuple[str, str] | None:
    found = _MEANING.match(_trimmed(core))
    if not found:
        return None
    return _result(found.group("tail") or MEANING_TARGET, found.group("text"))


def _lang_for(core: str) -> tuple[str, str] | None:
    found = _LANG_FOR.match(core)
    if not found:
        return None
    text = found.group("text")
    if found.group("what"):  # "what's the Spanish word for dog?": the "?" closes the question to me
        text = _trimmed(text)
    return _result("in " + found.group("lang"), text)


def _bare(core: str) -> tuple[str, str] | None:
    trimmed = _trimmed(core)
    if len(trimmed.split()) > BARE_MAX_WORDS:
        return None
    found = _BARE.match(trimmed)
    return _result(found.group("tail"), found.group("text")) if found else None


def _result(tail: str, text: str, allow_unknown: bool = False) -> tuple[str, str] | None:
    instruction = tail.strip(" ,")
    source = _unquoted(text)
    if not source or _only_filler(source) or not _is_language_tail(instruction, allow_unknown):
        return None
    return instruction, source


def _is_language_tail(tail: str, allow_unknown: bool) -> bool:
    words = _WORD.findall(tail.casefold())
    if not words or len(words) > TAIL_MAX_WORDS:
        return False
    if languages.find(tail)[0] is None:
        return allow_unknown and bool(request.unknown_language(tail)) and len(words) <= 3
    rest = _WORD.findall(languages.strip_names(tail).casefold())
    return all(word in _SETTING_WORDS for word in rest)


def _only_filler(text: str) -> bool:
    return all(word in _FILLER for word in _WORD.findall(text.casefold()))


def _trimmed(text: str) -> str:
    return _TRAIL.sub("", text).strip()


def _unquoted(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] in _Q and stripped[-1] in _Q:
        stripped = stripped[1:-1].strip()
    return stripped


def read_extraction(parsed: object) -> Extraction:
    """The scope call's JSON; anything unreadable is off_topic (fail closed)."""
    if not isinstance(parsed, dict):
        return Extraction("off_topic")
    status = parsed.get("status")
    if status == "text":
        return Extraction("translate")
    if status != "translate":
        return Extraction("off_topic")
    text, target = parsed.get("text"), parsed.get("target")
    return Extraction("translate", text if isinstance(text, str) else "", target if isinstance(target, str) else "")


def checked_extraction(extraction: Extraction, message: str) -> tuple[str, languages.Match | None, str]:
    """(source, target or None, unknown name or "") from a `translate` extraction. The text must appear in
    the message (case, spacing and quotes ignored), else the whole message is the text. A target that is not
    a known language is returned as `unknown`."""
    source = _unquoted(extraction.text)
    if not source or _normal(source) not in _normal(message):
        source = message.strip()
    name = extraction.target.strip()[:40]
    if not name:
        return source, None, ""
    found = languages.from_answer(name)
    return (source, found, "") if found else (source, None, name)


def _normal(text: str) -> str:
    return " ".join(re.sub(f"[{_Q}]", "", text or "").casefold().split())
