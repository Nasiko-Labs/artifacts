"""Responder notes → action_taken | no_action | unclear. Code keyword-classifies every distinct note first;
only the notes it cannot place go to the model, at most 80 per call. The model returns labels by id only:
unknown ids are ignored (and counted), a missing id stays `unclear`. No notes → no call."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import llm
import prompts
from signals import note_key

BATCH = 80
NOTE_MAX_CHARS = 300
KINDS = ("action_taken", "no_action", "unclear")
_ACTION = re.compile(
    r"\brestart(?:ed|ing)?\b|\brolled? ?back\b|\brollback\b|\bscaled\b|\bscale[ds]? up\b|\bfixed\b|\bdeployed\b|"
    r"\bincreased\b|\bcleared\b|\bfail(?:ed)? ?over\b|\bmitigat\w*|\bhotfix\w*|\bpatched\b|\broot cause\b|"
    r"\bticket\b|(?-i:\b[A-Z][A-Z0-9]{1,9}-\d+\b)|#\d+|\bescalat\w*", re.IGNORECASE)
_NO_ACTION = re.compile(
    r"\bfalse positive\b|\bno action\b|\bnoise\b|\bnoisy\b|\btransient\b|\bself[- ]?resolved\b|\bauto[- ]?resolved\b|"
    r"\bflap\w*|\bignored?\b|\bexpected\b|\bknown issue\b|\bnot actionable\b|\bnothing to do\b|\bduplicate\b|"
    r"\back and ignore\b", re.IGNORECASE)


def keyword_kind(note: str) -> str | None:
    """The kind code can decide, or None to ask the model."""
    if not note.strip():
        return None
    action, none = bool(_ACTION.search(note)), bool(_NO_ACTION.search(note))
    if action and none:
        return "unclear"
    if action:
        return "action_taken"
    if none:
        return "no_action"
    return None


@dataclass(frozen=True)
class NoteLabels:
    kinds: dict[str, str]            # note_key → kind
    by_model: int = 0                # notes the model labelled
    unlabelled: int = 0              # notes left unclear because a call failed or the budget ran out
    unknown_ids: int = 0             # ids in the model's answer that were never sent (dropped)
    off_topic: bool = False


def parse_labels(data: Any) -> dict | None:
    if isinstance(data, Mapping) and data.get("status") == "off_topic":
        return {"off_topic": True, "labels": []}
    labels = data.get("labels") if isinstance(data, Mapping) else None
    if not isinstance(labels, list):
        return None
    clean = [{"id": str(x["id"]), "kind": x["kind"]} for x in labels
             if isinstance(x, Mapping) and x.get("kind") in KINDS and x.get("id") is not None]
    return {"off_topic": False, "labels": clean}


async def label(notes: Iterable[str], deps: llm.TurnDeps, context: tuple[str, ...] = ()) -> NoteLabels:
    distinct: dict[str, str] = {}
    for note in notes:
        if note.strip():
            distinct.setdefault(note_key(note), note.strip())
    kinds: dict[str, str] = {}
    leftovers: list[tuple[str, str]] = []
    for key, text in distinct.items():
        kind = keyword_kind(text)
        if kind is None:
            leftovers.append((key, text))
        else:
            kinds[key] = kind
    by_model = unlabelled = unknown = 0
    off_topic = False
    for start in range(0, len(leftovers), BATCH):
        batch = leftovers[start:start + BATCH]
        ids = {f"n{start + i + 1}": key for i, (key, _) in enumerate(batch)}
        payload = [{"id": f"n{start + i + 1}", "text": text[:NOTE_MAX_CHARS]} for i, (_, text) in enumerate(batch)]
        try:
            parsed = await llm.complete_json(deps, prompts.notes_messages(payload, context), prompts.NOTES_CONTRACT,
                                             parse_labels)
        except llm.LlmError:
            unlabelled += len(batch)
            continue
        off_topic = off_topic or parsed["off_topic"]
        for item in parsed["labels"]:
            if item["id"] in ids:
                kinds[ids[item["id"]]] = item["kind"]
                by_model += 1
            else:
                unknown += 1
    for key, _ in leftovers:
        kinds.setdefault(key, "unclear")
    return NoteLabels(kinds, by_model, unlabelled, unknown, off_topic)
