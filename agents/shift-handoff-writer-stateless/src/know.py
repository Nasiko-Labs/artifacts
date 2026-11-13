"""Pure: the "Need to know" list, in code order: (1) instructions that say do not / never / must / before you
/ if ... then / when; (2) other standing instructions; (3) waiting items with a stated follow-up time;
(4) promises the notes record, with the time as written or "no time stated". The model's `know` order is
only a tie-break inside a class. Capped at 10 in the default view."""

from __future__ import annotations

import re
from dataclasses import dataclass

import duetime
from entries import Entry, without_stamp
from items import PROMISE, RESOLVED, WAITING, Instruction, Item, label
from status import item_texts

CAP = 10
NO_TIME = "no time stated"
STRONG = re.compile(r"\b(?:do not|don't|dont|never|must|before you|when)\b|\bif\b.+\bthen\b", re.IGNORECASE)


@dataclass(frozen=True)
class Know:
    kind: str           # instruction | promise | waiting
    text: str
    quote: str
    time: str | None = None
    ref: str = ""       # instruction id or item id

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "quote": self.quote, "time": self.time, "ref": self.ref}


def promise_time(instruction: Instruction) -> str | None:
    return duetime.time_mention(instruction.snippet) or duetime.time_mention(instruction.text)


def _waiting_line(item: Item, by_id: dict[str, Entry]) -> Know | None:
    if item.status != WAITING or item.status == RESOLVED:
        return None
    texts = item_texts(item, by_id)
    latest = without_stamp(texts[-1]) if texts else ""
    when = duetime.time_mention(item.next_step or "") or duetime.time_mention(latest)
    if not when:
        return None
    on = f"waiting on {item.waiting_on}" if item.waiting_on else "waiting"
    return Know("waiting", f"{label(item)} ({on})", item.snippet, when, item.id)


def build(instructions: tuple[Instruction, ...], items: tuple[Item, ...], by_id: dict[str, Entry],
          model_order: list[str] = ()) -> list[Know]:
    rank = {kid: n for n, kid in enumerate(model_order)}

    def tie(instruction: Instruction) -> tuple[int, int]:
        return rank.get(instruction.id, len(rank)), int(instruction.id.lstrip("k") or 0)

    plain = [i for i in instructions if i.kind != PROMISE]
    strong = sorted((i for i in plain if STRONG.search(i.text) or STRONG.search(i.snippet)), key=tie)
    other = sorted((i for i in plain if i not in strong), key=tie)
    promises = sorted((i for i in instructions if i.kind == PROMISE), key=tie)
    out = [Know("instruction", i.text, i.snippet, None, i.id) for i in (*strong, *other)]
    out += [k for k in (_waiting_line(item, by_id) for item in items) if k is not None]
    out += [Know("promise", i.text, i.snippet, promise_time(i), i.id) for i in promises]
    return out
