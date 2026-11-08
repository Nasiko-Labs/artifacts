"""Pure: wording rules for a narrative, checked in code. Analyst jargon, causes the user did not give,
alarm words, advice and forecasts, over-long sentences and the length cap. Each problem is a short
instruction the repair call can act on. A term the user used themselves is allowed."""

from __future__ import annotations

import re

from request import Ask

MAX_SENTENCE_WORDS = 30
JARGON = re.compile(r"\b(?:standard deviations?|std\.? dev|variance|quartiles?|percentiles?|z-?scores?|p-?values?|"
                    r"correlation coefficient|correlat\w*|regressions?|statistically significant|significance|"
                    r"yoy|y/y|year-over-year|mom|m/m|cagr|median|outliers?|interquartile|iqr|distribution|"
                    r"standard error|confidence interval|mean)\b", re.IGNORECASE)
CAUSAL = re.compile(r"\b(?:because|due to|caused by|causing|driven by|drove|as a result of|thanks to|owing to|"
                    r"led to|leads to|resulted in|results in|on the back of|fuel(?:l)?ed by)\b", re.IGNORECASE)
ALARM = re.compile(r"\b(?:plummet\w*|skyrocket\w*|soar\w*|crash\w*|collaps\w*|explod\w*|tank(?:ed|ing)|"
                   r"nosedive\w*|alarming\w*|shocking\w*|dramatic\w*|disastrous\w*|massive\w*|huge\w*)\b", re.IGNORECASE)
ADVICE = re.compile(r"\b(?:should|recommend\w*|we suggest|i suggest|consider (?:\w+ing)|will likely|is likely to|"
                    r"are likely to|expected to|expect(?:s)? (?:to|that)|forecast\w*|predict\w*|going forward|"
                    r"next (?:month|quarter|year) will|will (?:continue|keep|rise|fall|grow|drop))\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE.split((text or "").strip()) if s.strip()]


def words(text: str) -> int:
    return len((text or "").split())


def _new_terms(pattern: re.Pattern, text: str, instruction: str) -> list[str]:
    said = instruction.casefold()
    return list(dict.fromkeys(m.group(0) for m in pattern.finditer(text) if m.group(0).casefold() not in said))


def lint(headline: str, body: str, ask: Ask) -> list[str]:
    full = " ".join(x for x in (headline.strip(), body.strip()) if x)
    problems: list[str] = []
    jargon = _new_terms(JARGON, full, ask.instruction)
    if jargon:
        problems.append(f"say it in everyday words instead of: {', '.join(jargon)}")
    if not CAUSAL.search(ask.instruction):
        causes = _new_terms(CAUSAL, full, ask.instruction)
        if causes:
            problems.append(f"remove the cause ({', '.join(causes)}): the user gave none; say 'coincided with' or "
                            "drop it")
    for pattern, what in ((ALARM, "use calm words instead of"), (ADVICE, "no advice or forecasts; remove")):
        found = _new_terms(pattern, full, ask.instruction)
        if found:
            problems.append(f"{what}: {', '.join(found)}")
    long = [s for s in sentences(full) if words(s) > MAX_SENTENCE_WORDS]
    if long:
        problems.append(f"split sentences longer than {MAX_SENTENCE_WORDS} words")
    if words(full) > ask.cap:
        problems.append(f"shorten to at most {ask.cap} words in total (now {words(full)})")
    if ask.length == "one" and (body.strip() or len(sentences(headline)) > 1):
        problems.append("write exactly one sentence: the headline, with an empty narrative")
    return problems
