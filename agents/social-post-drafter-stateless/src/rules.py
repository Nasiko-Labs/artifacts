"""Pure: platform limits and the text checks computed in code (X weighted length, hooks, banned phrases,
emoji, exclamation marks, hashtags). The limits are conventions the platforms change; they are constants
here and labelled heuristics in the reply."""

from __future__ import annotations

import re
import unicodedata

LINKEDIN_HARD_CAP = 3_000
LINKEDIN_PREFERRED = (600, 1_300)
LINKEDIN_HOOK_MAX = 210          # heuristic: roughly what the feed shows before "see more"
X_MAX_WEIGHTED = 280
X_URL_WEIGHT = 23
THREAD_MIN, THREAD_MAX = 3, 8
MAX_EMOJI = 2
DEFAULT_HASHTAGS = 3
X_HASHTAGS = 1

PLATFORM_LABELS = {"linkedin": "LinkedIn", "x": "X", "x_thread": "X thread"}

_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()\]]+")
# X counts these ranges as one character each; everything else (CJK, emoji) as two.
_LIGHT_RANGES = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")
_EMOJI_JOINERS = re.compile("[️‍]")

BANNED_PHRASES = ("game-changer", "game changer", "revolutionary", "revolutionize", "unlock", "supercharge", "delve",
                  "leverage", "leveraging", "excited to announce", "thrilled", "synergy", "cutting-edge", "cutting edge", "seamless",
                  "next level", "dive into", "landscape", "in the realm of")
_BANNED = re.compile(r"\b(?:" + "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in BANNED_PHRASES)
                     + r")(?:s|d|ly|ing)?\b", re.IGNORECASE)
BANNED_OPENERS = ("i'm excited to announce", "i am excited to announce", "we're excited to announce",
                  "we are excited to announce", "thrilled to share", "in today's fast-paced world", "did you know",
                  "here's the thing")
_SUPERLATIVE = re.compile(r"\b(?:the|our|world'?s|industry'?s)\s+(?P<word>first|only|best|fastest|leading|biggest)\b"
                          r"|(?P<hash>#1)\b|\b(?P<num>number one)\b", re.IGNORECASE)
_HASHTAG = re.compile(r"(?<![\w&/])#([A-Za-z][A-Za-z0-9_]*)")
_HASHTAG_LINE = re.compile(r"^(?:\s*#[A-Za-z][A-Za-z0-9_]*[\s,]*)+$")
_NUMBERING = re.compile(r"\s*(?:\(?\b\d{1,2}\s?/\s?\d{1,2}\)?|🧵)\s*$|^\s*(?:\(?\d{1,2}\s?/\s?\d{1,2}\)?|🧵)\s*")


def x_length(text: str) -> int:
    """X's weighted length: a link counts 23, CJK and emoji 2, most other characters 1 (the usual rule;
    verify against X's current limits)."""
    normalised = unicodedata.normalize("NFC", text or "")
    links = _URL.findall(normalised)
    rest = _EMOJI_JOINERS.sub("", _URL.sub("", normalised))
    return len(links) * X_URL_WEIGHT + sum(1 if _light(ord(ch)) else 2 for ch in rest)


def _light(code: int) -> bool:
    return any(low <= code <= high for low, high in _LIGHT_RANGES)


def size(platform: str, text: str) -> int:
    return len(text) if platform == "linkedin" else x_length(text)


def emoji_count(text: str) -> int:
    return len(EMOJI.findall(text or ""))


def strip_emoji(text: str) -> str:
    out = _EMOJI_JOINERS.sub("", EMOJI.sub("", text or ""))
    return "\n".join(re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in out.split("\n")).strip()


def banned_hits(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).casefold() for m in _BANNED.finditer(text or "")))


def banned_opener(text: str) -> str | None:
    start = (text or "").strip().casefold().replace("’", "'")
    return next((o for o in BANNED_OPENERS if start.startswith(o)), None)


def exclamations(text: str) -> int:
    return (text or "").count("!")


def superlatives(text: str) -> list[re.Match]:
    return list(_SUPERLATIVE.finditer(text or ""))


def superlative_word(match: re.Match) -> str:
    return (match.group("word") or match.group("hash") or match.group("num")).casefold()


def first_line(text: str) -> str:
    return next((line.strip() for line in (text or "").split("\n") if line.strip()), "")


def split_hashtags(post: str) -> tuple[str, tuple[str, ...]]:
    """(body, tags): trailing hashtag-only lines are lifted out; an inline #Word keeps its word."""
    lines = (post or "").rstrip().split("\n")
    tags: list[str] = []
    while lines and _HASHTAG_LINE.match(lines[-1]) and lines[-1].strip():
        tags = [*_HASHTAG.findall(lines[-1]), *tags]
        lines = lines[:-1]
    body = _HASHTAG.sub(lambda m: m.group(1), "\n".join(lines)).rstrip()
    return body, tuple(tags)


def tag_name(raw: str) -> str:
    """CamelCase, letters and digits only ("product launch" -> "ProductLaunch")."""
    words = re.findall(r"[A-Za-z0-9]+", (raw or "").lstrip("#"))
    return "".join(w if w[:1].isupper() else w[:1].upper() + w[1:] for w in words)


def strip_numbering(post: str) -> str:
    return _NUMBERING.sub("", (post or "").strip()).strip()


def hashtag_limit(platform: str, voice_limit: int) -> int:
    return min(voice_limit, X_HASHTAGS) if platform in ("x", "x_thread") else voice_limit
