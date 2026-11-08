"""Pure: merge two or more same-schema JSON results (parts of one run) into one result with that schema.
Structural only: lists are concatenated (each item tagged with its `part`) and exact duplicates removed,
`summary`/`tldr`/`notes` strings are stitched and labelled, counts on a fixed list are summed, objects
are deep-merged, `confidence` is the lowest and `needs_human` the OR. No item's text is ever rewritten."""

from __future__ import annotations

from dataclasses import dataclass

import dedup

LEVELS = ("low", "medium", "high")
SUM_KEYS = frozenset({"words", "count", "total", "dropped", "left_out"})
STITCH_KEYS = frozenset({"summary", "tldr", "notes"})
RANKED_KEYS = frozenset({"ranked", "failure_modes"})
OWN_KEYS = frozenset({"confidence", "needs_human", "merge_notes", "merged_parts", "items_possibly_duplicate"})
MIN_JACCARD = 0.5
SUMMARY_MAX_CHARS = 600


@dataclass(frozen=True)
class MergeResult:
    status: str                     # ok | need_more | different
    objects: tuple[dict, ...]
    envelope: dict | None = None
    notes: tuple[str, ...] = ()


def merge(values: list, problems: tuple[str, ...] = ()) -> MergeResult:
    notes = list(problems)
    objects = []
    for n, value in enumerate(values, 1):
        if isinstance(value, dict):
            objects.append(value)
        else:
            notes.append(f"Result {n} is not a JSON object ({type(value).__name__}); ignored.")
    if len(objects) < 2:
        return MergeResult("need_more", tuple(objects), notes=tuple(notes))
    if not same_schema(objects):
        return MergeResult("different", tuple(objects), notes=tuple(notes))
    merged, uncertain = _merge_fields(objects, notes)
    merged["confidence"] = _confidence(objects, notes, uncertain) if _has(objects, "confidence") else None
    if merged["confidence"] is None:
        del merged["confidence"]
    merged["needs_human"] = any(bool(o.get("needs_human")) for o in objects) or len(objects) < len(values) \
        or bool(problems)
    merged["merged_parts"] = len(objects)
    merged["merge_notes"] = notes
    return MergeResult("ok", tuple(objects), merged, tuple(notes))


def same_schema(objects: list[dict]) -> bool:
    first = set(objects[0]) - OWN_KEYS
    for other in objects[1:]:
        keys = set(other) - OWN_KEYS
        union = first | keys
        if union and len(first & keys) / len(union) < MIN_JACCARD:
            return False
    return True


def _has(objects: list[dict], key: str) -> bool:
    return any(key in o for o in objects)


def _merge_fields(objects: list[dict], notes: list[str]) -> tuple[dict, bool]:
    alias = _items_alias(objects)
    out: dict = {}
    uncertain = False
    near_pairs = 0
    for key in _ordered_keys(objects):
        if key in OWN_KEYS or (key == "items" and alias):
            continue
        present = [(n, o[key]) for n, o in enumerate(objects, 1) if key in o]
        values = [v for _, v in present]
        if all(isinstance(v, list) for v in values):
            out[key], near = _merge_list(key, present, notes)
            near_pairs += near
        elif all(isinstance(v, str) for v in values) and key in STITCH_KEYS:
            out[key] = _stitch(key, present, len(objects))
        elif all(_is_number(v) for v in values):
            out[key], fell_back = _merge_numbers(key, values, notes)
            uncertain = uncertain or fell_back
        elif all(isinstance(v, dict) for v in values):
            out[key] = _deep_merge(values, key, notes)
        else:
            out[key] = _first_wins(key, values, notes)
    if alias:
        out["items"] = out.get(alias, [])
    if near_pairs:
        out["items_possibly_duplicate"] = near_pairs
        notes.append(f"possible duplicates: {near_pairs} pair(s) (kept; check them)")
    return out, uncertain or bool(near_pairs)


def _ordered_keys(objects: list[dict]) -> list[str]:
    keys: dict[str, None] = {}
    for o in objects:
        keys.update(dict.fromkeys(o))
    return list(keys)


def _items_alias(objects: list[dict]) -> str | None:
    """The named list every result's `items` equals, when it is the same name in all of them."""
    names = set()
    for o in objects:
        match = [k for k, v in o.items() if k != "items" and isinstance(v, list) and v == o.get("items")]
        if "items" not in o or not match:
            return None
        names.add(match[0])
    return names.pop() if len(names) == 1 else None


def _merge_list(key: str, present: list[tuple[int, list]], notes: list[str]) -> tuple[list, int]:
    joined = [_tag(item, n) for n, items in present for item in items]
    result = dedup.dedup(joined)
    items = list(result.items)
    if result.removed:
        notes.append(f"{result.removed} duplicate {key} removed")
    if key in RANKED_KEYS:
        items = [{**item, "rank": i} if isinstance(item, dict) else item for i, item in enumerate(items, 1)]
        notes.append(f"{key} re-numbered in paste order; scores from different parts were not re-compared "
                     "(re-run the Prioritizer for a true ranking)")
    return items, result.possible_pairs


def _tag(item, part: int):
    return {**item, "part": part} if isinstance(item, dict) and "part" not in item else item


def _stitch(key: str, present: list[tuple[int, str]], total: int) -> str:
    text = "\n\n".join(f"Part {n}: {v.strip()}" for n, v in present if v.strip())
    if key != "summary":
        return text
    head = f"Merged {total} parts (stitched, not rewritten): "
    room = SUMMARY_MAX_CHARS - len(head)
    return head + (text if len(text) <= room else text[:room - 1].rstrip() + "…")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _merge_numbers(key: str, values: list, notes: list[str]) -> tuple:
    if key in SUM_KEYS:
        return sum(values), False
    if len(set(values)) == 1:
        return values[0], False
    notes.append(f"{key} differs across parts and isn't a count, so it is kept as a list")
    return list(values), True


def _deep_merge(dicts: list[dict], path: str, notes: list[str]) -> dict:
    out: dict = {}
    for key in _ordered_keys(dicts):
        values = [d[key] for d in dicts if key in d]
        if all(isinstance(v, list) for v in values):
            out[key] = [item for v in values for item in v]
        elif all(isinstance(v, dict) for v in values):
            out[key] = _deep_merge(values, f"{path}.{key}", notes)
        else:
            out[key] = _first_wins(f"{path}.{key}", values, notes)
    return out


def _first_wins(key: str, values: list, notes: list[str]):
    present = [v for v in values if v is not None]
    if not present:
        return None
    if any(v != present[0] for v in present[1:]):
        notes.append(f"{key} differs across parts; kept the first")
    return present[0]


def _confidence(objects: list[dict], notes: list[str], uncertain: bool) -> str:
    levels = []
    for n, o in enumerate(objects, 1):
        value = str(o.get("confidence") or "").casefold()
        if value not in LEVELS:
            notes.append(f"Part {n} had no usable confidence; counted as medium.")
            value = "medium"
        levels.append(LEVELS.index(value))
    lowest = min(levels)
    return LEVELS[min(lowest, 1) if uncertain else lowest]
