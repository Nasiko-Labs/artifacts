"""The PR title and body: one model call (title, summary, why, risk notes) from the diff and the issue only,
then code checks. A title that is too long, not imperative or names things outside the diff falls back to one
built from the issue title; a sentence that claims testing, links somewhere or names a path, symbol or number
not in the diff or issue is removed. "How tested" is the computed test line; `Closes #n` is added by code only
when the issue number came from the user's own reference."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import llm
import prompts
from fence import fence
from llm import LlmError, TurnDeps

MAX_TITLE = 72
MAX_RISKS = 3
_TEST_CLAIM = re.compile(r"\btest(?:s|ed|ing)?\b.{0,30}\b(?:pass|passed|passing|green|succeed|ran|run)\b|"
                         r"\b(?:verified|validated)\b", re.IGNORECASE)
_LINK = re.compile(r"https?://\S+|www\.\S+")
_TICKET = re.compile(r"(?<![\w/])#\d+|\b[A-Z]{2,10}-\d+\b")
_PATHISH = re.compile(r"[\w.-]+/[\w./-]+\.\w+|\b[\w-]+\.(?:py|js|ts|tsx|go|java|rb|rs|cs|php)\b")
_IDENT = re.compile(r"`([^`]+)`")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_BANNED_TITLE = re.compile(r"\b(?:wip|urgent|asap|hotfix!+)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NON_IMPERATIVE = re.compile(r"^(?:fixed|fixes|added|adds|updated|updates|this pr|this change)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Composed:
    title: str
    summary: str
    why: str
    risk_notes: tuple[str, ...]
    dropped: int = 0
    used_model: bool = True


def fallback_title(issue_title: str) -> str:
    clean = " ".join((issue_title or "the issue").split())
    title = f"fix: {clean[:1].lower()}{clean[1:]}"
    return title if len(title) <= MAX_TITLE else title[:MAX_TITLE - 1].rstrip() + "…"


async def compose(deps: TurnDeps, issue_text: str, issue_title: str, diff: str) -> Composed:
    user = f"{fence('ISSUE', issue_text, 6_000)}\n\n{fence('DIFF', diff, 12_000)}"
    messages = [{"role": "system", "content": prompts.COMPOSE_SYSTEM}, {"role": "user", "content": user}]

    def validate(data: Any) -> dict | None:
        ok = isinstance(data, dict) and isinstance(data.get("title"), str) and isinstance(data.get("summary"), str)
        return data if ok or llm.is_off_topic(data) else None

    try:
        data = await llm.complete_json(deps, messages, prompts.contract_of(prompts.COMPOSE_SYSTEM), validate,
                                       max_tokens=700)
    except LlmError:
        return Composed(fallback_title(issue_title), "", "", (), 0, False)
    if llm.is_off_topic(data):
        return Composed(fallback_title(issue_title), "", "", (), 0, False)
    return check(data, issue_text, issue_title, diff)


def check(data: dict, issue_text: str, issue_title: str, diff: str) -> Composed:
    known = f"{issue_text}\n{diff}".casefold()
    dropped = 0
    title = " ".join(str(data.get("title") or "").split())
    if not _title_ok(title, known):
        title, dropped = fallback_title(issue_title), dropped + 1
    summary, n1 = _clean(str(data.get("summary") or ""), known, issue_text)
    why, n2 = _clean(str(data.get("why") or ""), known, issue_text)
    risks, n3 = [], 0
    for note in (data.get("risk_notes") or [])[:MAX_RISKS] if isinstance(data.get("risk_notes"), list) else []:
        kept, n = _clean(str(note), known, issue_text)
        n3 += n
        if kept:
            risks.append(kept)
    return Composed(title, summary, why, tuple(risks), dropped + n1 + n2 + n3)


def _title_ok(title: str, known: str) -> bool:
    if not title or len(title) > MAX_TITLE or _BANNED_TITLE.search(title) or _NON_IMPERATIVE.match(title):
        return False
    return not _LINK.search(title) and all(p.casefold() in known for p in _PATHISH.findall(title))


def _clean(text: str, known: str, issue_text: str) -> tuple[str, int]:
    """Sentences kept only when every path, `symbol`, number and ticket they name is in the diff or issue,
    and they claim no test result and carry no link."""
    kept, dropped = [], 0
    for sentence in _SENTENCE.split(" ".join(text.split())):
        if not sentence:
            continue
        named = _PATHISH.findall(sentence) + _IDENT.findall(sentence)
        bad = _TEST_CLAIM.search(sentence) or _LINK.search(sentence) \
            or any(t not in issue_text for t in _TICKET.findall(sentence)) \
            or any(n.casefold() not in known for n in named) \
            or any(n not in known for n in _NUMBER.findall(sentence))
        if bad:
            dropped += 1
            continue
        kept.append(sentence)
    return " ".join(kept), dropped


def body(composed: Composed, tests_line: str, issue_number: int | None, files: list[str]) -> str:
    """The PR body, built in code around the model's checked sentences."""
    summary = composed.summary or "Changes " + ", ".join(f"`{f}`" for f in files) + "."
    parts = ["## Summary", summary]
    if composed.why:
        parts += ["", "## Why", composed.why]
    parts += ["", "## How tested", tests_line]
    if composed.risk_notes:
        parts += ["", "## Risk notes", *[f"- {r}" for r in composed.risk_notes]]
    if issue_number is not None:
        parts += ["", f"Closes #{issue_number}"]
    parts += ["", "_Draft opened by the issue-to-pr agent after step-by-step approval; please review._"]
    return "\n".join(parts)
