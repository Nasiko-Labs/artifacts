"""Pure: checking a reply against the measured style and the channel register, and the code fixes applied
when the one repair call did not clear them. Checks never drop a reply."""

from __future__ import annotations

import re
from dataclasses import dataclass

import textstats as ts
from register import Register
from style import NONE, Metrics, greeting_pattern, length_window, sign_off_of

CHECKED = ("length", "greeting", "sign-off", "contractions", "exclamations", "emoji", "bullets", "case", "em dash")
_PARAGRAPHS = re.compile(r"\n\s*\n")
_PUNCT_END = re.compile(r"[\s.,!]+$")


@dataclass(frozen=True)
class Violation:
    check: str        # one of CHECKED, or "cap" / "paragraphs" for the channel's own limits
    message: str      # worded for the repair call


@dataclass(frozen=True)
class Target:
    metrics: Metrics
    register: Register
    sender: str = ""          # the incoming sender's first name, for {first}
    relaxed: bool = False     # the user asked for a different style this turn ("more formal", "shorter")
    short: bool = False


def expected_greeting(target: Target) -> str:
    return target.metrics.greeting.replace("{first}", target.sender or "[name?]")


def violations(body: str, target: Target) -> list[Violation]:
    m, reg = target.metrics, target.register
    found = _register_violations(body, reg)
    if not m.measured or target.relaxed:
        return found
    words = ts.word_count(body)
    low, high = length_window(m, target.short)
    if not low <= words <= high:
        found.append(Violation("length", f"the reply is {words} words; your examples average {m.avg_words} "
                                         f"words: keep it between {low} and {high} words"))
    found += _greeting(body, target) + _sign_off(body, target)
    found += _habits(body, m, reg, words)
    return found


def _register_violations(body: str, reg: Register) -> list[Violation]:
    found = []
    if (cap := reg.over_cap(body)) is not None:
        found.append(Violation("cap", cap))
    if reg.max_paragraphs and len(_PARAGRAPHS.split(body.strip())) > reg.max_paragraphs:
        found.append(Violation("paragraphs", f"a {reg.label.lower()} has at most {reg.max_paragraphs} paragraph(s)"))
    if not reg.sign_off and sign_off_of(body) != NONE:
        found.append(Violation("sign-off", f"a {reg.label.lower()} has no sign-off: remove the closing line"))
    if not reg.bullets and ts.has_bullets(body):
        found.append(Violation("bullets", f"a {reg.label.lower()} has no bullet points: use sentences"))
    return found


def _greeting(body: str, target: Target) -> list[Violation]:
    m = target.metrics
    if not target.register.greeting:
        return []
    if m.greeting == NONE:
        if greeting_pattern(body) != NONE:
            return [Violation("greeting", "your examples open without a greeting: start straight in")]
        return []
    expected = expected_greeting(target)
    if target.sender:
        ok = body.lstrip().startswith(expected)
    else:
        ok = greeting_pattern(body).casefold() == m.greeting.casefold()
    return [] if ok else [Violation("greeting", f"open with '{expected}' exactly, as your examples do")]


def _sign_off(body: str, target: Target) -> list[Violation]:
    m, reg = target.metrics, target.register
    if not reg.sign_off:
        return []
    if m.sign_off == NONE:
        return [Violation("sign-off", "your examples have no sign-off: end without one")] \
            if sign_off_of(body) != NONE else []
    if _norm(body).endswith(_norm(m.sign_off)):
        return []
    return [Violation("sign-off", f"end with '{m.sign_off.replace(chr(10), ' / ')}' exactly, as your examples do")]


def _norm(text: str) -> str:
    return _PUNCT_END.sub("", re.sub(r"\s+", " ", text.casefold()))


def _habits(body: str, m: Metrics, reg: Register, words: int) -> list[Violation]:
    found = []
    count = ts.contractions(body)
    if m.contraction_rate >= 2 and count == 0:
        found.append(Violation("contractions", "your examples use contractions (it's, can't); the reply has none"))
    if m.contraction_rate == 0 and count:
        found.append(Violation("contractions", "your examples never use contractions: write them out"))
    allowed = ts.allowed_exclamations(m.exclaim_rate, words)
    if body.count("!") > allowed:
        found.append(Violation("exclamations", f"use at most {allowed} exclamation mark(s); the reply has "
                                               f"{body.count('!')}"))
    if ts.has_emoji(body) and not m.emoji:
        found.append(Violation("emoji", "your examples have no emoji: remove them"))
    if reg.bullets and ts.has_bullets(body) and not m.bullets:
        found.append(Violation("bullets", "your examples never use bullet points: use sentences"))
    if m.lowercase_start and body.strip()[:1].isupper():
        found.append(Violation("case", "your examples start with a lowercase letter"))
    if m.avoids_em_dash and "—" in body:
        found.append(Violation("em dash", "your examples never use em dashes: use a comma or a full stop"))
    return found


def fix(body: str, target: Target) -> str:
    """What code can fix: emoji, em dashes, extra exclamation marks, bullets, case, a forbidden sign-off,
    paragraphs on a text message. Length, greeting and contractions stay as written (and are noted)."""
    m, reg = target.metrics, target.register
    out = body
    if not reg.sign_off and sign_off_of(out) != NONE:
        out = _drop_sign_off(out)
    if not m.emoji or not reg.bullets:
        out = re.sub(r"[ \t]{2,}", " ", ts.EMOJI.sub("", out)) if not m.emoji else out
    if not reg.bullets or (m.measured and not m.bullets):
        out = ts.BULLET.sub(r"\1", out)
    if reg.max_paragraphs == 1:
        out = " ".join(p.strip().replace("\n", " ") for p in _PARAGRAPHS.split(out.strip()))
    if m.measured and not target.relaxed:
        out = _fix_habits(out, m)
    return out.strip()


def _fix_habits(body: str, m: Metrics) -> str:
    out = body.replace(" — ", ", ").replace("—", ", ") if m.avoids_em_dash else body
    allowed = ts.allowed_exclamations(m.exclaim_rate, ts.word_count(out))
    while out.count("!") > allowed:
        idx = out.rfind("!")
        out = out[:idx] + "." + out[idx + 1:]
    if m.lowercase_start and out[:1].isupper():
        out = out[:1].lower() + out[1:]
    return out


def _drop_sign_off(body: str) -> str:
    lines = body.rstrip().split("\n")
    closing = sign_off_of(body)
    if "\n" in closing and len(lines) > 2:
        return "\n".join(lines[:-2]).rstrip()
    if len(lines) > 1 and lines[-1].strip() == closing:
        return "\n".join(lines[:-1]).rstrip()
    return body.rstrip()[: len(body.rstrip()) - len(closing)].rstrip()
