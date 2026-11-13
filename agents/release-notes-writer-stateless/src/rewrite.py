"""The rewrite+theme step: one model call per 40 customer-facing changes (sentence + theme each), then the
code checks, at most one repair call for the sentences that failed, and the title fallback.

The model's `kind` is used only for a change whose prefix and labels named none; its `customer_facing`
only for those too. A chunk that could not be written (deadline, failed call) keeps its raw titles."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import prompts
from banned import remove_banned
from change import Change
from grounding import clean_title, is_soft, sentence_problems
from inputs import Header
from llm import LlmError, TurnDeps, call, complete_json, load

log = logging.getLogger("release-notes.rewrite")

MAP_CHUNK = 40
MAX_THEMES = 5
MAX_THEME_WORDS = 3
OTHER = "Other"
MODEL_KINDS = ("new", "improved", "fixed", "deprecated")
_MARKUP = re.compile(r"<[^>\n]+>|!\[[^\]]*\]\([^)]*\)|`{3,}")


@dataclass(frozen=True)
class Rewritten:
    changes: tuple[Change, ...]
    themes: tuple[str, ...] = ()
    off_topic: bool = False
    unfinished: int = 0


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic"}
    items = data.get("items")
    if not isinstance(items, list):
        return None
    themes = data.get("themes") if isinstance(data.get("themes"), list) else []
    return {"status": "ok", "themes": themes, "items": [i for i in items if isinstance(i, dict)]}


def merge_themes(known: Sequence[str], proposed: Sequence[Any]) -> tuple[str, ...]:
    merged = list(known)
    for raw in proposed:
        name = " ".join(str(raw or "").split()).strip(" .")
        if not name or len(name.split()) > MAX_THEME_WORDS or len(merged) >= MAX_THEMES:
            continue
        if name.casefold() not in {t.casefold() for t in merged} and name.casefold() != OTHER.casefold():
            merged.append(name)
    return tuple(merged)


def theme_for(raw: Any, themes: Sequence[str]) -> str:
    wanted = " ".join(str(raw or "").split()).casefold()
    return next((t for t in themes if t.casefold() == wanted), OTHER)


def block(change: Change) -> str:
    return prompts.change_block(change.id, change.title, change.body, change.labels, change.kind)


def _chunks(items: Sequence[Change], size: int) -> list[list[Change]]:
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def clean_sentence(raw: Any) -> str:
    """The model's sentence as plain text: no HTML, no images, no code fences."""
    return " ".join(_MARKUP.sub(" ", str(raw or "")).split())


def _fallback(change: Change) -> Change:
    return replace(change, sentence=clean_title(change), fallback=True, grounded=False)


def _resolve(change: Change, sentence: str, problems: list[str]) -> Change:
    if not sentence or any(not is_soft(p) for p in problems):
        return _fallback(change)
    jargon = any(p.startswith("jargon:") for p in problems)
    text = remove_banned(sentence) if any(p.startswith("banned:") for p in problems) else sentence
    return replace(change, sentence=text, grounded=True, fallback=False, jargon=jargon)


def _decided(change: Change, answer: dict, themes: Sequence[str]) -> Change:
    updates: dict[str, Any] = {"theme": theme_for(answer.get("theme"), themes)}
    if change.kind is None:
        kind = answer.get("kind")
        updates["kind"] = kind if kind in MODEL_KINDS else "improved"
        updates["internal"] = answer.get("customer_facing") is False
    return replace(change, **updates)


async def rewrite(deps: TurnDeps, changes: Sequence[Change], header: Header, policy: Sequence[str] = (),
                  instruction: str = "") -> Rewritten:
    targets = [c for c in changes if not c.internal and not c.dropped]
    themes: tuple[str, ...] = ()
    answers: dict[str, dict] = {}
    unfinished: set[str] = set()
    for chunk in _chunks(targets, MAP_CHUNK):
        messages = prompts.rewrite_messages([block(c) for c in chunk], header.audience, header.tone, themes,
                                            instruction, policy)
        try:
            data = await complete_json(deps, messages, prompts.REWRITE_CONTRACT, _validate)
        except LlmError as e:
            log.warning("rewrite chunk failed: %s", e)
            unfinished |= {c.id for c in chunk}
            continue
        if data["status"] == "off_topic":
            return Rewritten(tuple(changes), off_topic=True)
        themes = merge_themes(themes, data["themes"])
        answers.update({str(i.get("id")): i for i in data["items"]})
    updated: dict[str, Change] = {}
    failing: dict[str, tuple[Change, str, list[str]]] = {}
    for change in targets:
        if change.id in unfinished:
            updated[change.id] = _fallback(change)
            continue
        answer = answers.get(change.id, {})
        decided = _decided(change, answer, themes)
        sentence = clean_sentence(answer.get("sentence"))
        problems = sentence_problems(sentence, decided, header.text(), header.audience) if sentence else []
        if sentence and problems:
            failing[change.id] = (decided, sentence, problems)
        updated[change.id] = _resolve(decided, sentence, problems)
    updated.update(await _repair(deps, failing, header, policy))
    final = tuple(updated.get(c.id, c) for c in changes)
    return Rewritten(final, themes, unfinished=len(unfinished))


async def _repair(deps: TurnDeps, failing: dict[str, tuple[Change, str, list[str]]], header: Header,
                  policy: Sequence[str]) -> dict[str, Change]:
    """One repair call for every failed sentence, if the turn's repair is unused; then resolve again."""
    if not failing or not deps.has_time() or not deps.budget.take_repair():
        return {}
    picked = dict(list(failing.items())[:MAP_CHUNK])
    messages = prompts.repair_messages([block(c) for c, _, _ in picked.values()],
                                       {cid: p for cid, (_, _, p) in picked.items()}, header.audience, policy)
    try:
        data = load(await call(deps, messages))
    except LlmError as e:
        log.warning("repair failed: %s", e)
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    fixed = {str(i.get("id")): i for i in items or () if isinstance(i, dict)}
    out: dict[str, Change] = {}
    for cid, (change, _, _) in picked.items():
        sentence = clean_sentence((fixed.get(cid) or {}).get("sentence"))
        problems = sentence_problems(sentence, change, header.text(), header.audience) if sentence else []
        out[cid] = _resolve(change, sentence, problems)
    return out
