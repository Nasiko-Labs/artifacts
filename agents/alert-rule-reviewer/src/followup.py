"""Follow-ups on the stored review. Commands are read in code and cost no call ("only high", "only AR004",
"show the config for HighCPU", "proposed changes only", "why is AR004 a problem", "as json"); a question
about one alert is one scoped call over that alert; anything else is one call that may come back off_topic.

The model's answer is checked in code: quotes must be verbatim in the data, sentences with a number that is
not in the data are dropped, links not in the data are removed."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import catalog
import prompts
import render
from inputs import strip_json_words
from llm import LlmError, TurnDeps, complete_json

ANSWER_MAX_CHARS = 1_200
DATA_MAX_CHARS = 12_000

_SEVERITY = re.compile(r"\b(?:only|just)\s+(?:the\s+)?(high|medium|low)\b|\b(high|medium|low)(?:\s+(?:ones|findings|"
                       r"severity|issues))?\s+only\b", re.IGNORECASE)
_EXPLAIN = re.compile(r"\b(?:why|what|explain|mean|meaning|problem|matter)\b", re.IGNORECASE)
_CONFIG_FOR = re.compile(r"\b(?:show|see|print|give)\b.*?\bconfig(?:uration)?\s+(?:for|of)\s+[`'\"]?([\w:.\-]+)",
                         re.IGNORECASE)
_CHANGES = re.compile(r"\b(?:proposed\s+)?changes\s+only\b|\bonly\s+(?:the\s+)?(?:proposed\s+)?changes\b|"
                      r"\bjust\s+(?:the\s+)?(?:proposed\s+)?changes\b|\bshow\s+(?:me\s+)?the\s+(?:proposed\s+)?changes\b",
                      re.IGNORECASE)
_NOISY = re.compile(r"\b(?:only|just)\s+(?:the\s+)?noisy\b|\bnoisy\s+(?:ones\s+)?only\b", re.IGNORECASE)
_FULL = re.compile(r"^(?:show\s+(?:all|everything|sources|the\s+(?:full\s+)?review)|full\s+review|again|"
                   r"show\s+evidence)\W*$", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>()\[\]`]*[^\s<>()\[\]`.,;:!?'\"]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Command:
    kind: str                     # view | explain | config
    view: render.View | None = None
    rule: str = ""
    alert: str = ""


def command(text: str, alert_names: Sequence[str]) -> Command | None:
    """A follow-up code can answer with no call, or None."""
    plain = strip_json_words(text).strip()
    rule = catalog.RULE_ID.search(plain)
    if rule and _EXPLAIN.search(plain):
        return Command("explain", rule=rule.group(1).upper())
    config = _CONFIG_FOR.search(plain)
    if config:
        return Command("config", alert=_match_name(config.group(1), alert_names) or config.group(1))
    severity = _SEVERITY.search(plain)
    if severity:
        return Command("view", render.View(severity=(severity.group(1) or severity.group(2)).lower()))
    if rule:
        return Command("view", render.View(rule=rule.group(1).upper()))
    if _CHANGES.search(plain):
        return Command("view", render.View(section="changes"))
    if _NOISY.search(plain):
        return Command("view", render.View(section="noisy"))
    if _FULL.match(plain) or (not plain and text.strip()):
        return Command("view", render.View())
    return None


def _match_name(word: str, names: Sequence[str]) -> str | None:
    return next((n for n in names if n.casefold() == word.strip("`'\".,?").casefold()), None)


def mentioned_alert(text: str, names: Sequence[str]) -> str | None:
    for name in sorted(names, key=len, reverse=True):
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
            return name
    return None


def _validate(data) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in ("ok", "off_topic", "needs_config"):
        return None
    if data["status"] == "ok" and not isinstance(data.get("answer"), str):
        return None
    return data


def checked_answer(answer: dict, data: str) -> tuple[str, list[str], int]:
    """(answer text, verbatim quotes, dropped count) after the code checks."""
    dropped = 0
    text = _URL.sub(lambda m: m.group(0) if m.group(0) in data else "", str(answer.get("answer") or ""))
    sentences = []
    for sentence in _SENTENCE.split(text[:ANSWER_MAX_CHARS]):
        if all(n in data for n in _NUMBER.findall(sentence)):
            sentences.append(sentence)
        else:
            dropped += 1
    quotes = []
    for quote in answer.get("quotes") or []:
        if isinstance(quote, str) and quote.strip() and quote.strip() in data:
            quotes.append(quote.strip())
        elif isinstance(quote, str) and quote.strip():
            dropped += 1
    return " ".join(sentences).strip(), quotes[:5], dropped


async def ask(deps: TurnDeps, question: str, data: str, policy: Sequence[str]) -> tuple[str, str]:
    """(status, reply). status: ok | off_topic | needs_config | failed."""
    try:
        answer = await complete_json(deps, prompts.question_messages(question, data[:DATA_MAX_CHARS], policy),
                                     prompts.QUESTION_CONTRACT, _validate)
    except LlmError:
        return "failed", prompts.MODEL_FAILED
    if answer["status"] != "ok":
        return answer["status"], ""
    text, quotes, dropped = checked_answer(answer, data)
    parts = [render.safe(text) or "I can't answer that from the pasted config."]
    if quotes:
        parts.append("From your config:\n" + "\n".join(f"- {render.code(q)}" for q in quotes))
    if dropped:
        parts.append(f"_{dropped} part(s) of the answer were left out because they could not be checked against "
                     "your paste._")
    return "ok", "\n\n".join(parts)
