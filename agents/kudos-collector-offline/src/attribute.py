"""Who each record credits. PR authors and explicit feature owners are read in code (no model call). Praise and
features without owners go to the model, 25 records per call, 4 at a time; every name it returns must be
written in the record (as words, or an `@handle` the alias map joins to it) or it is dropped. When the model
can't be used, names are taken in code: `@handles` and already-known full names written in the record."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import llm
import prompts
from batching import chunks, gather_bounded
from identity import appears_in, normalise
from llm import TurnDeps
from records import PR, PRAISE, SHIPPED, Record
from shapes import HANDLE

BATCH = 25
WHAT_MAX_WORDS = 25
CODE, MODEL, FALLBACK = "code", "model", "fallback"


@dataclass(frozen=True)
class Attribution:
    people: tuple[str, ...]
    what: str = ""
    source: str = CODE

    def to_dict(self) -> dict:
        return {"people": list(self.people), "what": self.what, "source": self.source}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Attribution:
        return Attribution(tuple(str(p) for p in data.get("people") or ()), str(data.get("what") or ""),
                           str(data.get("source") or CODE))


def credited_text(record: Record) -> str:
    """Where a credited name must appear: a praise message's words after its speaker; the whole record else."""
    return record.body if record.kind == PRAISE else record.text


def by_code(record: Record) -> Attribution | None:
    if record.kind == PR:
        return Attribution((record.author,) if record.author else ())
    if record.kind == SHIPPED and record.owners:
        return Attribution(record.owners)
    return None


def verify(names: Sequence[str], record: Record, aliases: dict[str, str]) -> tuple[str, ...]:
    text = credited_text(record)
    kept: dict[str, str] = {}
    for name in names:
        clean = " ".join(str(name).split())
        if clean and appears_in(clean, text, aliases):
            kept.setdefault(normalise(clean), clean)
    return tuple(kept.values())


def fallback_people(record: Record, known: Sequence[str], aliases: dict[str, str]) -> tuple[str, ...]:
    """Code-only names: `@handles`, alias handles written as words, and known full names in the record."""
    text = credited_text(record)
    found = list(HANDLE.findall(text))
    hay = f" {normalise(text)} "
    found += [name for handle, name in aliases.items() if f" {normalise(handle)} " in hay]
    found += [n for n in known if len(normalise(n).split()) >= 2 and f" {normalise(n)} " in hay]
    return tuple(dict.fromkeys(found))


def _validator(ids: set[str]):
    def validate(data: Any) -> dict[str, tuple[list[str], str]] | None:
        if not isinstance(data, Mapping) or not isinstance(data.get("records"), list):
            return None
        out = {}
        for entry in data["records"]:
            if not isinstance(entry, Mapping) or str(entry.get("id")) not in ids:
                continue
            people = entry.get("people")
            people = [str(p) for p in people if isinstance(p, str)] if isinstance(people, list) else []
            what = " ".join(str(entry.get("what") or "").split()[:WHAT_MAX_WORDS])
            out[str(entry["id"])] = (people, what)
        return out
    return validate


def _item(record: Record) -> dict:
    return {"id": record.id, "kind": record.kind, "speaker": record.speaker, "record": record.text}


async def attribute(deps: TurnDeps, records: Sequence[Record], aliases: dict[str, str], known: Sequence[str],
                    context: Sequence[str] = ()) -> dict[str, Attribution]:
    out: dict[str, Attribution] = {}
    need = []
    for r in records:
        found = by_code(r)
        if found is None:
            need.append(r)
        else:
            out[r.id] = found

    async def run(batch: list[Record]) -> dict[str, Attribution]:
        try:
            answer = await llm.complete_json(deps, prompts.attribute_messages([_item(r) for r in batch], context),
                                             _validator({r.id for r in batch}))
        except llm.LlmError:
            answer = {}
        result = {}
        for r in batch:
            if r.id in answer:
                people, what = answer[r.id]
                result[r.id] = Attribution(verify(people, r, aliases), what, MODEL)
            else:
                result[r.id] = Attribution(fallback_people(r, known, aliases), "", FALLBACK)
        return result

    if need and deps.traceparent:
        for part in await gather_bounded(chunks(need, BATCH), run):
            out.update(part)
    for r in need:
        out.setdefault(r.id, Attribution(fallback_people(r, known, aliases), "", FALLBACK))
    return out


def has_any_name(records: Sequence[Record]) -> bool:
    """Before any model call: could anyone be identified at all? (authors, owners, handles, or a capitalised
    word inside a praise message that is not its first word)."""
    for r in records:
        if r.author or r.owners or HANDLE.search(r.text):
            return True
        words = re.findall(r"[A-Za-z][\w'-]*", credited_text(r))
        if any(w[0].isupper() and w.casefold() not in _COMMON for w in words[1:]):
            return True
    return False


_COMMON = frozenset({"i", "we", "the", "a", "thanks", "thank", "great", "slack", "team", "everyone", "all", "monday",
                     "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "ok", "pr", "prs", "api", "ui",
                     "csv", "pdf", "sso", "qa", "ci", "q1", "q2", "q3", "q4"})
