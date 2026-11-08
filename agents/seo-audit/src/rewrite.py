"""Title / meta description / JSON-LD proposals (one model call + at most one length repair). Code measures
every length, removes numbers the page does not contain, and cleans the JSON-LD (fields not on the page are
removed; a block that does not parse is omitted). A proposal still over its cap is shown with a note."""

from __future__ import annotations

import re
from typing import Any

import jsonld
import prompts
from fence import strip_markers
from llm import LlmError, TurnDeps, complete_json
from models import PageFacts, Proposals
from pagetext import normalise, verbatim

TITLE_MAX = 60
META_RANGE = (120, 155)
TEXT_CHARS = 3_000
CONTRACT = '{"title": str, "meta": str, "json_ld": object or null}'
_NUMBER = re.compile(r"\d[\d,.]*\d|\d")


async def propose(deps: TurnDeps, facts: PageFacts, text: str, html: str, keyword: str, topic: str,
                  want_json_ld: bool, context: tuple[str, ...], title_only: bool = False) -> Proposals:
    if deps.budget.left <= 0:
        return Proposals(notes=["Rewrites skipped: this turn's model-call budget is spent."])
    page = {"current_title": facts.title.text if facts.title else "",
            "current_meta": facts.meta_description.text if facts.meta_description else "",
            "h1": [h.text for h in facts.h1s], "headings": [t for _, t in facts.outline[:15]],
            "target": keyword or topic, "brand": facts.site_name if verbatim(facts.site_name, text, html) else ""}
    messages = prompts.rewrite_messages(page, strip_markers(text[:TEXT_CHARS]), want_json_ld, context)
    try:
        data = await complete_json(deps, messages, CONTRACT, _validate)
    except LlmError:
        return Proposals(notes=["Rewrites unavailable this time (the model call failed)."])
    notes: list[str] = []
    title, meta = _grounded(data["title"], text, html, notes), _grounded(data["meta"], text, html, notes)
    meta = "" if title_only else meta
    violations = _violations(title, meta)
    if violations and deps.budget.left > 0:
        try:
            fixed = await complete_json(deps, prompts.length_repair_messages({"title": title, "meta": meta},
                                                                             violations), CONTRACT, _validate)
            title = _grounded(fixed["title"], text, html, notes)
            meta = "" if title_only else _grounded(fixed["meta"], text, html, notes)
        except LlmError:
            notes.append("The length repair failed.")
    notes.extend(_violations(title, meta))
    json_ld, removed = jsonld.clean_proposal(data.get("json_ld"), lambda v: verbatim(v, text, html)) \
        if want_json_ld else (None, 0)
    if removed:
        notes.append(f"{removed} JSON-LD field(s) not supported by the page were removed.")
    return Proposals(title=title, title_chars=len(title), meta=meta, meta_chars=len(meta), json_ld=json_ld or "",
                     notes=list(dict.fromkeys(notes)))


async def retitle(deps: TurnDeps, facts: PageFacts, text: str, html: str, keyword: str,
                  context: tuple[str, ...]) -> Proposals:
    """Follow-up "rewrite the title for <keyword>": the same call, no JSON-LD."""
    return await propose(deps, facts, text, html, keyword, keyword, False, context, title_only=True)


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("title"), str) or not isinstance(data.get("meta"), str):
        return None
    return data


def _grounded(value: str, text: str, html: str, notes: list[str]) -> str:
    clean = " ".join(strip_markers(value).split())
    haystack = normalise(text + " " + html)
    for number in set(_NUMBER.findall(clean)):
        if number not in haystack:
            clean = re.sub(rf"(?<![\d,.]){re.escape(number)}(?![\d,.])", "", clean)
            notes.append(f"Removed the number {number}: it is not on the page.")
    return " ".join(clean.split()).strip(" -|:,")


def _violations(title: str, meta: str) -> list[str]:
    out = []
    if len(title) > TITLE_MAX:
        out.append(f"Proposed title is too long: {len(title)} chars, cap {TITLE_MAX}.")
    if len(meta) > META_RANGE[1]:
        out.append(f"Proposed meta description is too long: {len(meta)} chars, cap {META_RANGE[1]}.")
    elif meta and len(meta) < META_RANGE[0]:
        out.append(f"Proposed meta description is short: {len(meta)} chars, aim for {META_RANGE[0]}-{META_RANGE[1]}.")
    return out
