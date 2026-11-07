"""Pure: does a model sentence say only what the pasted change supports?

A sentence's numbers, versions, dates, URLs, identifiers and capitalised names must appear in the change
text (or the header), and at least half of its content words must come from the change text (stems,
stopwords and a small list of plain verbs excepted). The same rules, against the validated sentences, hold
the Slack intro and the email opening and closing to what the notes already say."""

from __future__ import annotations

import re

from banned import find_banned, find_jargon, has_exploit_detail, has_future, has_urgency
from change import Change

MAX_SENTENCE_WORDS = 25
MIN_OVERLAP = 0.5
MIN_PREFIX_STEM = 4

_PLAIN_WORDS = """a an the and or but of to in on at by for from with into onto over under about as is are was were
be been being it its it's this that these those there their they them you your yours we our us can cannot can't
will would should could may might now no not longer anymore again when while where which who what how also only
just any all each every other some more less than then so if after before during without through across up down out
use uses used using see sees get gets set sets add adds added fix fixes fixed remove removes removed rename renames
renamed require requires required improve improves improved update updates updated change changes changed stop
stops stopped work works working worked show shows shown showing let lets allow allows able option options
available support supports supported correctly properly right issue issues problem problems bug bugs error errors
instead new now shipped patched patch addressed resolved resolves affected area make makes made has have had do
does did done one ones way ways"""
PLAIN = frozenset(_PLAIN_WORDS.split())
BREAKING_VERB = re.compile(r"\b(?:removed|renamed|now requires?|no longer|replaced|changed|moved|deprecated|"
                           r"requires)\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*%?")
_URL = re.compile(r"https?://[^\s)>\]]+")
_IDENT = re.compile(r"`([^`]+)`|(?<![\w/])(/[\w./-]+|\w+_[\w_]+|\w+/\w[\w/.-]*)")
_SENTENCE_START = re.compile(r"(?:^|[.!?:]\s+|\n)\s*$")
_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "ied", "ed", "es", "s", "ly")


def stem(word: str) -> str:
    w = word.casefold().strip("'-")
    for suffix in _SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)]
    return w


def _stems(text: str) -> set[str]:
    return {stem(w) for w in _WORD.findall(text or "")}


def _matches(word_stem: str, source: set[str]) -> bool:
    if word_stem in source:
        return True
    return len(word_stem) >= MIN_PREFIX_STEM and any(
        len(s) >= MIN_PREFIX_STEM and (s.startswith(word_stem) or word_stem.startswith(s)) for s in source)


def overlap(sentence: str, source: str) -> float:
    words = [w for w in _WORD.findall(sentence or "") if w.casefold() not in PLAIN and len(w) >= 3]
    if not words:
        return 1.0
    source_stems = _stems(source)
    return sum(1 for w in words if _matches(stem(w), source_stems)) / len(words)


def _in(token: str, text: str) -> bool:
    return token.casefold().strip(".,;:") in (text or "").casefold()


def unsupported(text: str, allowed: str) -> list[str]:
    """Numbers, URLs, identifiers and capitalised names in `text` that `allowed` does not contain."""
    found = [f"url:{u}" for u in _URL.findall(text or "") if not _in(u, allowed)]
    rest = _URL.sub(" ", text or "")
    found += [f"number:{n}" for n in _NUMBER.findall(rest) if not _in(n, allowed)]
    for match in _IDENT.finditer(rest):
        ident = (match.group(1) or match.group(2) or "").strip(".,;:")
        if ident and not _in(ident, allowed):
            found.append(f"identifier:{ident}")
    for match in _WORD.finditer(rest):
        word = match.group(0)
        if not word[0].isupper() or word == "I" or _SENTENCE_START.search(rest[: match.start()]):
            continue
        if not re.search(rf"\b{re.escape(word.casefold())}\b", (allowed or "").casefold()):
            found.append(f"name:{word}")
    return found


def sentence_problems(sentence: str, change: Change, header_text: str = "", audience: str = "all") -> list[str]:
    text = " ".join((sentence or "").split())
    if not text:
        return ["empty"]
    source = f"{change.source_text()} {header_text}"
    problems: list[str] = []
    if len(text.split()) > MAX_SENTENCE_WORDS:
        problems.append("too_long")
    problems += [f"banned:{w}" for w in find_banned(text)]
    if audience != "developers":
        problems += [f"jargon:{w}" for w in find_jargon(text) if not _in(w, change.title)]
    problems += unsupported(text, source)
    if overlap(text, source) < MIN_OVERLAP:
        problems.append("overlap")
    if change.breaking and not BREAKING_VERB.search(text):
        problems.append("breaking_verb")
    if has_future(text):
        problems.append("future")
    if change.kind == "security" and has_exploit_detail(text) and not has_exploit_detail(source):
        problems.append("exploit")
    return problems


def is_soft(problem: str) -> bool:
    """Problems that never force the title fallback: banned words are removed, jargon is flagged."""
    return problem.startswith(("banned:", "jargon:"))


def free_text_problems(text: str, allowed: str, max_words: int) -> list[str]:
    """For the Slack intro and email opening/closing/subject: grounded in the notes, the header and the
    organization's own instructions; no mentions, no urgency, no future promises."""
    clean = " ".join((text or "").split())
    if not clean:
        return ["empty"]
    problems = ["too_long"] if len(clean.split()) > max_words else []
    if "@" in clean or "<!" in clean:
        problems.append("mention")
    if has_urgency(clean):
        problems.append("urgency")
    if has_future(clean):
        problems.append("future")
    return problems + unsupported(clean, allowed)


def clean_title(change: Change) -> str:
    """The fallback sentence: the change's own title, prefix and PR number already removed."""
    title = " ".join(change.title.split()).rstrip(" .")
    return (title[:1].upper() + title[1:]) if title else change.ref or change.id
