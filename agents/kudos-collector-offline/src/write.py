"""One example per usable record (PRs, specific praise, features), 40 records per call, 4 at a time. Each example
is checked in code (grounding.py); failing ones get one repair pass (at most two calls), and anything still
failing falls back to the cleaned title or the verbatim quote."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import grounding
import llm
import prompts
from batching import chunks, gather_bounded
from llm import TurnDeps
from records import Record

BATCH = 40
MAX_REPAIR_CALLS = 2
EXAMPLE_MAX_CHARS = 400
MODEL, REPAIRED, FALLBACK = "model", "repaired", "fallback"


@dataclass(frozen=True)
class Written:
    text: str
    source: str

    def to_dict(self) -> dict:
        return {"text": self.text, "source": self.source}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Written:
        return Written(str(data.get("text") or ""), str(data.get("source") or FALLBACK))


def _validator(ids: set[str]):
    def validate(data: Any) -> dict[str, str] | None:
        if not isinstance(data, Mapping) or not isinstance(data.get("examples"), list):
            return None
        out = {}
        for entry in data["examples"]:
            if isinstance(entry, Mapping) and str(entry.get("id")) in ids and isinstance(entry.get("example"), str):
                out[str(entry["id"])] = " ".join(entry["example"].split())[:EXAMPLE_MAX_CHARS]
        return out
    return validate


def _item(record: Record, people: Sequence[str]) -> dict:
    return {"id": record.id, "kind": record.kind, "credited": list(people), "record": record.text}


async def _ask(deps: TurnDeps, messages: list[dict], ids: set[str]) -> dict[str, str]:
    try:
        return await llm.complete_json(deps, messages, _validator(ids))
    except llm.LlmError:
        return {}


async def write(deps: TurnDeps, items: Sequence[tuple[Record, Sequence[str]]],
                context: Sequence[str] = ()) -> dict[str, Written]:
    by_id = {r.id: r for r, _ in items}
    people = {r.id: list(p) for r, p in items}
    drafts: dict[str, str] = {}
    if deps.traceparent and items:
        async def run(batch: list[tuple[Record, Sequence[str]]]) -> dict[str, str]:
            return await _ask(deps, prompts.write_messages([_item(r, p) for r, p in batch], context),
                              {r.id for r, _ in batch})
        for part in await gather_bounded(chunks(list(items), BATCH), run):
            drafts.update(part)
    out: dict[str, Written] = {}
    failing: dict[str, list[str]] = {}
    for rid, text in drafts.items():
        found = grounding.problems(text, by_id[rid])
        if found:
            failing[rid] = found
        else:
            out[rid] = Written(text, MODEL)
    if failing:
        out.update(await _repair(deps, failing, drafts, by_id, people, context))
    for rid, record in by_id.items():
        out.setdefault(rid, Written(grounding.fallback(record), FALLBACK))
    return out


async def _repair(deps: TurnDeps, failing: dict[str, list[str]], drafts: dict[str, str],
                  by_id: dict[str, Record], people: dict[str, list[str]], context: Sequence[str]) -> dict[str, Written]:
    ids = list(failing)[:BATCH * MAX_REPAIR_CALLS]
    fixed: dict[str, Written] = {}
    for batch in chunks(ids, BATCH):
        items = [{**_item(by_id[i], people[i]), "previous": drafts[i], "problems": failing[i]} for i in batch]
        answer = await _ask(deps, prompts.repair_messages(items, context), set(batch))
        for rid, text in answer.items():
            if not grounding.problems(text, by_id[rid]):
                fixed[rid] = Written(text, REPAIRED)
    return fixed
