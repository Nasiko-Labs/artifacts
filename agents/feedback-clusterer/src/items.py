"""Pure: one feedback item (a single customer message) and how it is built from parsed fields."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import fields as f


@dataclass(frozen=True)
class FeedbackItem:
    id: str
    text: str
    customer: str = ""
    date: str = ""
    tier: str = "unknown"
    arr: float | None = None
    source: str = ""
    ref: str = ""

    @property
    def customer_key(self) -> str:
        return f.customer_key(self.customer)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> FeedbackItem:
        arr = data.get("arr")
        return FeedbackItem(id=str(data["id"]), text=str(data.get("text") or ""), customer=str(data.get("customer") or ""),
                            date=str(data.get("date") or ""), tier=str(data.get("tier") or "unknown"),
                            arr=float(arr) if arr is not None else None, source=str(data.get("source") or ""),
                            ref=str(data.get("ref") or ""))


def numbered(items: list[FeedbackItem] | tuple[FeedbackItem, ...], start: int = 1) -> tuple[FeedbackItem, ...]:
    return tuple(replace(item, id=f"i{n}") for n, item in enumerate(items, start=start))


def make_item(text: str, customer: str = "", day: str = "", tier: str = "", arr: object = None, source: str = "",
              ref: str = "") -> FeedbackItem:
    parsed_day = f.parse_date(day)
    return FeedbackItem(id="", text=text.strip(), customer=customer.strip(),
                        date=parsed_day.isoformat() if parsed_day else "", tier=f.normalise_tier(tier),
                        arr=f.parse_arr(arr), source=str(source or "").strip(), ref=str(ref or "").strip())


def dedupe(items: tuple[FeedbackItem, ...]) -> tuple[tuple[FeedbackItem, ...], int]:
    """Exact duplicates (same customer, same text once whitespace and case are ignored) count once."""
    seen: set[tuple[str, str]] = set()
    kept: list[FeedbackItem] = []
    for item in items:
        key = (item.customer_key, " ".join(item.text.casefold().split()))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return tuple(kept), len(items) - len(kept)
