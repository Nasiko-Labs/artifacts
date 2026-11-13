"""Pure: turn the model's proposed items into checked items.

- A snippet must appear (normalised, >= 12 chars) in the LATEST entry the item cites; if it is in another
  entry instead, the item is re-pointed there; if it is in no entry, the item is dropped.
- An item may not span two clauses of one run-on line (`;`-split siblings): it is cut back to the clause its
  snippet is in, plus any sibling that names the same ticket. Each clause is its own update.
- owner, customer, due and refs must appear as written in the item's own entries, else they are cleared (never
  filled in): a customer or ticket named in another entry is not this item's, so two items never merge on a
  ref borrowed from another clause.
- An "instruction" whose entry has no instruction or promise wording (if/when/do not/check/call/told/...) is the
  model misfiling an update; it is dropped, and the backstop below keeps the update as an item.
- Backstop: every entry no item or instruction cites (nor shares a ticket ref with an item) becomes an item
  quoted verbatim, except a closing remark ("nothing else pending"), so no update can vanish because the model
  skipped it or because its clause was cut off a mixed item.
- Items that share a ref are merged: the latest entry wins for words, status and next step; the earlier
  snippets are kept as `history` quotes. Every entry that mentions an item's ref is attached to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import grounding
from entries import Entry, find_refs, is_nothing_pending, ref_key, without_stamp
from items import INSTRUCTION, Instruction, Item

MAX_HISTORY = 3
DIRECTIVE = re.compile(r"\b(?:do not|don't|never|must|before you|make sure)\b", re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.;!?])\s+")
INSTRUCTION_WORDING = re.compile(
    r"\b(?:if|when|unless|once|do not|don't|never|must|make sure|before|after you|remember|please|check|call|"
    r"follow up|remind|ping|tell|told|promised|we'?d|we'?ll|will|should|need to|point them)\b", re.IGNORECASE)


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


def _own_entries(home: Entry, cited: list[Entry]) -> list[Entry]:
    """The cited entries this item may span: never a sibling clause, unless it names the home clause's ticket."""
    keys = {ref_key(r) for r in home.refs}
    kept = [e for e in cited if not home.is_sibling(e) or keys & {ref_key(r) for r in e.refs}]
    return sorted({*kept, home}, key=lambda e: e.order)


def _item(raw: dict, by_id: dict[str, Entry], all_entries: list[Entry]) -> Item | None:
    cited = sorted((by_id[e] for e in raw["entries"] if e in by_id), key=lambda e: e.order)
    home, moved = _locate(raw["snippet"], cited, all_entries)
    if home is None:
        return None
    entries = _own_entries(home, cited)
    own = grounding.normalise("\n".join(e.text for e in entries))
    refs = [r for r in raw.get("refs") or [] if grounding.present(own, r)]
    refs += [r for r in find_refs(raw["snippet"]) if ref_key(r) not in {ref_key(x) for x in refs}]
    if not refs and len(home.refs) == 1:
        refs = list(home.refs)
    kept = {k: (raw.get(k) if grounding.present(own, raw.get(k)) else None)
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


def _uncovered(items: list[Item], instructions: list[Instruction], entries: list[Entry]) -> list[Item]:
    used = {e for i in items for e in i.entries} | {i.entry for i in instructions}
    covered_refs = {ref_key(r) for i in items for r in i.refs}
    out = []
    for entry in entries:
        text = without_stamp(entry.text).strip()
        if entry.id in used or covered_refs & {ref_key(r) for r in entry.refs} or is_nothing_pending(text):
            continue
        snippet = grounding.clip(text)
        out.append(Item(id="", what=f'"{snippet}"', snippet=snippet, snippet_entry=entry.id, entries=(entry.id,),
                        refs=entry.refs, fallback=True))
    return out


def reconcile(raw_items: list[dict], raw_instructions: list[dict], entries: list[Entry],
              existing: tuple[Item, ...] = (), existing_instructions: tuple[Instruction, ...] = ()) -> Reconciled:
    by_id = {e.id: e for e in entries}
    checked = [i for i in (_item(r, by_id, entries) for r in raw_items) if i is not None]
    located = [i for i in (instruction(r, by_id, entries) for r in raw_instructions) if i is not None]
    found = [i for i in located if INSTRUCTION_WORDING.search(by_id[i.entry].text)]
    merged = merge_by_ref([*existing, *checked, *_uncovered([*existing, *checked], found, entries)], entries)
    order = {e.id: e.order for e in entries}
    merged.sort(key=lambda i: order[i.entries[0]])
    items = tuple(replace(item, id=f"i{n}") for n, item in enumerate(merged, start=1))
    every = _unique([*existing_instructions, *found])
    every += directives(entries, every)
    numbered = tuple(replace(i, id=f"k{n}") for n, i in enumerate(every, start=1))
    dropped = len(raw_items) - len(checked) + len(raw_instructions) - len(located)
    return Reconciled(items, numbered, dropped)
