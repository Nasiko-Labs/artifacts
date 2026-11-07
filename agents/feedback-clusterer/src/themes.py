"""Model steps that name themes and assign items: discover (map), merge (one call), assign (map).

The model sees item ids and text only, never a customer, a count or an amount. Code validates every
answer: unknown ids are ignored, bad names are replaced, types are checked against the enum."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

import fields as f
import llm
import prompts
from batching import MAX_ASSIGN_CALLS, MAX_DISCOVERY_CALLS, batches
from filters import THEME_TYPES
from items import FeedbackItem

log = logging.getLogger("feedback_clusterer.themes")

MAX_NAME_WORDS = 6
MAX_NAME_CHARS = 60
MAX_DEFINITION_CHARS = 160
MAX_THEMES = 12
MERGE_RESERVE_SECS = 200.0  # discovery stops this long before the deadline, leaving room for the rest


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Candidate(_Loose):
    name: str
    definition: str = ""
    type: str = "other"
    item_ids: list[str] = []


class _Discovered(_Loose):
    status: str = "ok"
    themes: list[_Candidate] = []


class _Canonical(_Loose):
    id: str = ""
    name: str
    definition: str = ""
    type: str = "other"
    merges: list[str] = []


class _Merged(_Loose):
    themes: list[_Canonical]


class _Assignment(_Loose):
    id: str
    theme_id: str | None = "none"


class _Assigned(_Loose):
    assignments: list[_Assignment]


def _validator(model: type[BaseModel]):
    def validate(data: Any) -> dict | None:
        try:
            return model.model_validate(data).model_dump()
        except ValidationError:
            return None
    return validate


@dataclass(frozen=True)
class Discovery:
    candidates: tuple[dict, ...]
    off_topic: bool = False
    failed: int = 0


@dataclass(frozen=True)
class Assignment:
    theme_of: dict[str, str]     # item id -> theme id (items with "none" are absent)
    unreached: tuple[str, ...]   # item ids whose batch was cut by the deadline or failed
    cut: bool = False


async def discover(deps: llm.TurnDeps, pool: Sequence[FeedbackItem], hint: str,
                   context: Sequence[str] = ()) -> Discovery:
    groups = batches(pool)[:MAX_DISCOVERY_CALLS]

    def job(group: list[FeedbackItem]):
        async def run() -> dict | None:
            try:
                return await llm.complete_json(deps, prompts.discover_messages(group, hint, context),
                                               prompts.DISCOVER_CONTRACT, _validator(_Discovered))
            except llm.LlmError:
                return None
        return run

    results, _ = await llm.bounded([job(g) for g in groups], deps.remaining() - MERGE_RESERVE_SECS)
    answered = [r for r in results if r is not None]
    candidates = []
    for result in answered:
        for theme in result["themes"] if result["status"] == "ok" else ():
            if theme["name"].strip() and len(theme["name"]) <= MAX_NAME_CHARS:
                candidates.append({"id": f"c{len(candidates) + 1}", "name": theme["name"].strip(),
                                   "definition": theme["definition"].strip()[:MAX_DEFINITION_CHARS],
                                   "type": _type(theme["type"])})
    off_topic = bool(answered) and all(r["status"] == "off_topic" for r in answered)
    return Discovery(tuple(candidates), off_topic, len(results) - len(answered))


async def merge(deps: llm.TurnDeps, candidates: Sequence[dict], customer_keys: set[str],
                context: Sequence[str] = ()) -> list[dict]:
    """Canonical themes t1..tn; falls back to the checked candidates if the call or every theme fails."""
    known = {c["id"] for c in candidates}
    try:
        parsed = await llm.complete_json(deps, prompts.merge_messages(candidates, context), prompts.MERGE_CONTRACT,
                                         _validator(_Merged))
        merged = [{"name": t["name"].strip(), "definition": t["definition"].strip()[:MAX_DEFINITION_CHARS],
                   "type": _type(t["type"]), "merges": [m for m in t["merges"] if m in known]}
                  for t in parsed["themes"] if len(t["name"].split()) <= MAX_NAME_WORDS and t["name"].strip()]
    except llm.LlmError:
        log.warning("merge failed; using the discovered candidates")
        merged = []
    if not merged:
        merged = [dict(c, merges=[c["id"]]) for c in candidates if len(c["name"].split()) <= MAX_NAME_WORDS]
    return clean_names(merged[:MAX_THEMES], customer_keys)


def clean_names(themes: Sequence[dict], customer_keys: set[str]) -> list[dict]:
    """Ids t1..tn; a name that is a customer or holds an email is replaced; duplicate names get a suffix."""
    seen: dict[str, int] = {}
    out = []
    for n, theme in enumerate(themes, start=1):
        name = prompts.strip_markers(theme["name"])[:MAX_NAME_CHARS].strip()
        if not name or f.contains_email(name) or f.customer_key(name) in customer_keys:
            name = f"Unnamed theme {n}"
        key = name.casefold()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            name = f"{name} ({seen[key]})"
        out.append({"id": f"t{n}", "name": name, "definition": theme.get("definition", ""), "type": theme["type"]})
    return out


async def assign(deps: llm.TurnDeps, items: Sequence[FeedbackItem], themes: Sequence[dict],
                 context: Sequence[str] = ()) -> Assignment:
    theme_ids = {t["id"] for t in themes}
    groups = batches(items)[:MAX_ASSIGN_CALLS]

    def job(group: list[FeedbackItem]):
        async def run() -> dict | None:
            try:
                return await llm.complete_json(deps, prompts.assign_messages(group, themes, context),
                                               prompts.ASSIGN_CONTRACT, _validator(_Assigned))
            except llm.LlmError:
                return None
        return run

    results, cut = await llm.bounded([job(g) for g in groups], deps.remaining() - llm.ASSIGN_RESERVE_SECS)
    theme_of: dict[str, str] = {}
    unreached: list[str] = [i.id for group in batches(items)[MAX_ASSIGN_CALLS:] for i in group]
    for group, result in zip(groups, results, strict=True):
        if result is None:
            unreached += [i.id for i in group]
            continue
        in_batch = {i.id for i in group}
        for entry in result["assignments"]:
            if entry["id"] in in_batch and entry["theme_id"] in theme_ids and entry["id"] not in theme_of:
                theme_of[entry["id"]] = entry["theme_id"]
    return Assignment(theme_of, tuple(unreached), bool(cut))


def _type(raw: str) -> str:
    plain = (raw or "").strip().casefold().replace(" ", "_").replace("-", "_")
    return plain if plain in THEME_TYPES else "other"
