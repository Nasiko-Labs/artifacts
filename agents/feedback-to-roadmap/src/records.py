"""Records and the JSON contracts of every model step. Pure: parsing and validation only.

`Item` is one customer message (a desk ticket, a chat message or a pasted block). The model's raw answers
are validated here with pydantic; anything that does not fit is None, never a guess."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

_FENCED_JSON = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Item:
    id: str
    source: str            # desk | chat | pasted
    text: str
    customer: str = ""
    date: str = ""         # ISO date or ""
    tier: str = "unknown"
    arr: float | None = None
    url: str = ""
    ref: str = ""          # the source's own id
    label: str = ""        # the extract step's label; "" = none

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Item:
        known = {f.name for f in fields(Item)}
        return Item(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class LinkedIssue:
    id: str
    title: str
    url: str
    state: str
    score: float

    @property
    def closed(self) -> bool:
        return self.state.casefold().strip() in CLOSED_STATES


CLOSED_STATES = frozenset({"done", "closed", "cancelled", "canceled", "completed", "resolved", "released", "shipped",
                           "duplicate", "won't do", "wont do"})


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RawTicket(_Loose):
    id: str | int
    url: str | None = ""
    created_at: str | None = ""
    customer: str | None = ""
    text_excerpt: str | None = ""


class RawTickets(_Loose):
    status: str = "ok"
    items: list[RawTicket] = []


class RawMessage(_Loose):
    id: str | int
    url: str | None = ""
    posted_at: str | None = ""
    author_label: str | None = ""
    is_bot: bool = False
    text: str | None = ""


class RawMessages(_Loose):
    status: str = "ok"
    items: list[RawMessage] = []


class RawLabel(_Loose):
    item_id: str
    label: str = ""
    is_question: bool = False


class RawLabels(_Loose):
    labels: list[RawLabel]


class RawTheme(_Loose):
    name: str
    definition: str = ""
    type: str = "other"
    labels: list[str] = []


class RawThemes(_Loose):
    themes: list[RawTheme]


class RawPick(_Loose):
    quote_item_ids: list[str]


class RawSignal(_Loose):
    id: str
    is_churn_signal: bool = False
    kind: str = "frustration"


class RawSignals(_Loose):
    signals: list[RawSignal]


class RawCustomer(_Loose):
    name: str
    company_id: str | int | None = ""
    url: str | None = ""
    arr_raw: str | int | float | None = ""


class RawCustomers(_Loose):
    customers: list[RawCustomer] = []


class RawIssue(_Loose):
    id: str | int
    title: str = ""
    url: str | None = ""
    state: str | None = ""


class RawIssues(_Loose):
    issues: list[RawIssue] = []


class RawScope(_Loose):
    intent: str


def loads_object(text: str) -> Any:
    """The first JSON object in a model answer (a ```json block is unwrapped), or None."""
    raw = (text or "").strip()
    match = _FENCED_JSON.search(raw)
    if match:
        raw = match.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except ValueError:
        return None


def parse(model: type[BaseModel], text: str) -> Any:
    """A validated contract object, or None."""
    data = loads_object(text)
    if data is None:
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


def parser(model: type[BaseModel]):
    return lambda text: parse(model, text)
