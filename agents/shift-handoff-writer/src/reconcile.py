"""Pure: turn the model's proposed items into checked items.

- A snippet must appear (normalised, >= 12 chars) in the LATEST entry the item cites; if it is in another
  entry instead, the item is re-pointed there; if it is in no entry, the item is dropped.
- owner, customer, due, refs must appear in the notes as written, else they are cleared (never filled in).
- Items that share a ref are merged: the latest entry wins for words, status and next step; the earlier
  snippets are kept as `history` quotes. Every entry that mentions an item's ref is attached to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import grounding
from entries import Entry, find_refs, ref_key, without_stamp
from items import INSTRUCTION, Instruction, Item

MAX_HISTORY = 3
DIRECTIVE = re.compile(r"\b(?:do not|don't|never|must|before you|make sure)\b", re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.;!?])\s+")


@dataclass(frozen=True)
class Reconciled:
    items: tuple[Item, ...]
    instructions: tuple[Instruction, ...]
    dropped: int


def _locate(snippet: str, cited: list[Entry], all_entries: list[Entry]) -> tuple[Entry | None, bool]:
    if cited and grounding.contains(cited[-1].text, snippet):
        return cited[-1], False
    for entry in reversed(all_entries):
        if grounding.contains(entry.text, snippet):
            return entry, True
    return None, False


def _item(raw: dict, by_id: dict[str, Entry], all_entries: list[Entry], notes_norm: str) -> Item | None:
    cited = sorted((by_id[e] for e in raw["entries"] if e in by_id), key=lambda e: e.order)
    home, moved = _locate(raw["snippet"], cited, all_entries)
    if home is None:
        return None
    entries = sorted({*cited, home}, key=lambda e: e.order)
    refs = [r for r in raw.get("refs") or [] if grounding.present(notes_norm, r)]
    refs += [r for r in find_refs(raw["snippet"]) if ref_key(r) not in {ref_key(x) for x in refs}]
    if not refs and len(home.refs) == 1:
        refs = list(home.refs)
    kept = {k: (raw.get(k) if grounding.present(notes_norm, raw.get(k)) else None)
            for k in ("owner", "customer", "due", "status_words")}
    return Item(id="", what=raw["what"], snippet=grounding.clip(raw["snippet"]), snippet_entry=home.id,
                entries=tuple(e.id for e in entries), refs=tuple(dict.fromkeys(refs)), next_step=raw.get("next_step"),
                repointed=moved, **kept)


def _merge(older: Item, newer: Item, order: dict[str, int]) -> Item:
    if order[newer.snippet_entry] < order[older.snippet_entry]:
        older, newer = newer, older
    history = [*older.history, older.snippet, *newer.history]
    keys = {ref_key(r) for r in newer.refs}
    return replace(newer, entries=tuple(sorted({*older.entries, *newer.entries}, key=order.__getitem__)),
                   refs=(*newer.refs, *(r for r in older.refs if ref_key(r) not in keys)),
                   owner=newer.owner or older.owner, customer=newer.customer or older.customer,
                   due=newer.due or older.due, repointed=older.repointed or newer.repointed,
                   marked=newer.marked or older.marked,
                   history=tuple(dict.fromkeys(h for h in history if h != newer.snippet))[-MAX_HISTORY:])


def merge_by_ref(items: list[Item], entries: list[Entry]) -> list[Item]:
    order = {e.id: e.order for e in entries}
    merged: list[Item] = []
    for item in items:
        keys = {ref_key(r) for r in item.refs}
        match = next((i for i, m in enumerate(merged) if keys & {ref_key(r) for r in m.refs}), None)
        if match is None:
            merged.append(item)
        else:
            merged[match] = _merge(merged[match], item, order)
    return [_attach(item, entries) for item in merged]


def _attach(item: Item, entries: list[Entry]) -> Item:
    keys = {ref_key(r) for r in item.refs}
    if not keys:
        return item
    order = {e.id: e.order for e in entries}
    extra = {e.id for e in entries if keys & {ref_key(r) for r in e.refs}}
    return replace(item, entries=tuple(sorted({*item.entries, *extra}, key=order.__getitem__)))


def instruction(raw: dict, by_id: dict[str, Entry], all_entries: list[Entry]) -> Instruction | None:
    cited = [by_id[raw["entry"]]] if raw["entry"] in by_id else []
    home, moved = _locate(raw["snippet"], cited, all_entries)
    if home is None:
        return None
    return Instruction(id="", text=raw["text"], kind=raw.get("kind") or INSTRUCTION, entry=home.id,
                       snippet=grounding.clip(raw["snippet"]), fallback=moved)


def _unique(found: list[Instruction]) -> list[Instruction]:
    by_snippet: dict[str, Instruction] = {}
    for item in found:
        by_snippet.setdefault(grounding.normalise(item.snippet), item)
    return list(by_snippet.values())


def directives(entries: list[Entry], known: list[Instruction]) -> list[Instruction]:
    """Code backstop for recall: a sentence in the notes that gives a hard rule ("do not close #4801 until
    ...") is an instruction even when the model missed it. Quoted verbatim, never paraphrased."""
    seen = [grounding.normalise(i.snippet).strip(" .") for i in known]
    out = []
    for entry in entries:
        for sentence in _SENTENCES.split(without_stamp(entry.text)):
            norm = grounding.normalise(sentence).strip(" .")
            if len(norm) < grounding.MIN_SNIPPET_CHARS or not DIRECTIVE.search(sentence):
                continue
            if any(norm in s or s in norm for s in seen):
                continue
            seen.append(norm)
            clean = grounding.clip(sentence.strip())
            out.append(Instruction(id="", text=clean, kind=INSTRUCTION, entry=entry.id, snippet=clean))
    return out


def reconcile(raw_items: list[dict], raw_instructions: list[dict], entries: list[Entry],
              existing: tuple[Item, ...] = (), existing_instructions: tuple[Instruction, ...] = ()) -> Reconciled:
    by_id = {e.id: e for e in entries}
    notes_norm = grounding.normalise("\n".join(e.text for e in entries))
    checked = [i for i in (_item(r, by_id, entries, notes_norm) for r in raw_items) if i is not None]
    merged = merge_by_ref([*existing, *checked], entries)
    order = {e.id: e.order for e in entries}
    merged.sort(key=lambda i: order[i.entries[0]])
    items = tuple(replace(item, id=f"i{n}") for n, item in enumerate(merged, start=1))
    found = [i for i in (instruction(r, by_id, entries) for r in raw_instructions) if i is not None]
    every = _unique([*existing_instructions, *found])
    every += directives(entries, every)
    numbered = tuple(replace(i, id=f"k{n}") for n, i in enumerate(every, start=1))
    dropped = len(raw_items) - len(checked) + len(raw_instructions) - len(found)
    return Reconciled(items, numbered, dropped)
