"""Pure: the records the lookups produce (owner, channel), the LLM JSON contracts (pydantic), and number/date
formatting shared by rendering and the draft checks. Every number shown is formatted here, from code values."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from correlate import Change
from export import MICROS

OwnerSource = Literal["tag", "author", "unknown"]
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class Owner:
    display_name: str
    handle: str
    chat_id: str
    link: str
    source: OwnerSource

    @property
    def known(self) -> bool:
        return self.source != "unknown" and bool(self.chat_id)

    @property
    def label(self) -> str:
        return self.display_name or self.handle or "[owner]"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Any) -> Owner:
        if not isinstance(data, dict):
            return UNKNOWN_OWNER
        source = data.get("source") if data.get("source") in ("tag", "author") else "unknown"
        return Owner(str(data.get("display_name") or ""), str(data.get("handle") or ""),
                     str(data.get("chat_id") or ""), str(data.get("link") or ""), source)


UNKNOWN_OWNER = Owner("", "", "", "", "unknown")


@dataclass(frozen=True)
class Channel:
    id: str
    name: str

    @property
    def label(self) -> str:
        return "#" + self.name.lstrip("#") if self.name else self.id


# --- LLM JSON contracts -------------------------------------------------------------------------------------

class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RepoOut(_Loose):
    full_name: str
    link: str | None = None


class ReposOut(_Loose):
    repos: list[RepoOut] = []


class ChangeOut(_Loose):
    repo: str
    kind: str
    id: str | int
    title: str
    author_login: str | None = None
    at: str
    link: str


class ChangesOut(_Loose):
    changes: list[ChangeOut] = []


class OwnerOut(_Loose):
    display_name: str = ""
    handle: str = ""
    chat_id: str
    link: str | None = None
    matched: str = "team"


class ChannelOut(_Loose):
    id: str
    name: str = ""


class OwnerLookupOut(_Loose):
    owner: OwnerOut | None = None
    channels: list[ChannelOut] = []


class DraftOut(_Loose):
    body: str


def load_json_object(text: str) -> dict | None:
    """The JSON object in a model answer (bare, or in a ```json block), else None."""
    stripped = (text or "").strip()
    fenced = _JSON_BLOCK.search(stripped)
    body = fenced.group(1) if fenced else stripped
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_model[T: BaseModel](model: type[T], text: str) -> T | None:
    data = load_json_object(text)
    if data is None:
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


# --- formatting -----------------------------------------------------------------------------------------------

def _decimal(value: Fraction | int) -> Decimal:
    f = Fraction(value)
    with localcontext() as ctx:
        ctx.prec = 40
        return Decimal(f.numerator) / Decimal(f.denominator) / Decimal(MICROS)


def money(micros: Fraction | int) -> str:
    """Micro-units → "2,310.00" (half-up, 2 decimals)."""
    return f"{_decimal(micros).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def signed_money(micros: Fraction | int) -> str:
    text = money(micros)
    return text if text.startswith("-") else "+" + text


def percent(share: Fraction | None) -> str:
    """0.6191 → "62%"; None → "n/a"."""
    if share is None:
        return "n/a"
    value = (Decimal(share.numerator) / Decimal(share.denominator) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return f"{value}%"


def signed_percent(share: Fraction | None) -> str:
    text = percent(share)
    return text if text == "n/a" or text.startswith("-") else "+" + text


def day_label(d: date) -> str:
    """2026-03-09 → "Mar 9"."""
    return f"{d.strftime('%b')} {d.day}"


@dataclass(frozen=True)
class Findings:
    """What the lookups found this conversation, all checked in code. Kept in the graph state between turns."""

    repos: tuple[str, ...] = ()
    changes: tuple[Change, ...] = ()
    owner: Owner = UNKNOWN_OWNER
    channels: tuple[Channel, ...] = ()
    notes: tuple[str, ...] = ()
    code_checked: bool = False      # the deploy lookup ran over at least one repository
    draft: str = ""

    def to_dict(self) -> dict:
        return {"repos": list(self.repos), "changes": [c.to_dict() for c in self.changes],
                "owner": self.owner.to_dict(), "channels": [asdict(c) for c in self.channels],
                "notes": list(self.notes), "code_checked": self.code_checked, "draft": self.draft}

    @staticmethod
    def from_dict(data: Any) -> Findings:
        if not isinstance(data, dict):
            return Findings()
        return Findings(repos=tuple(str(r) for r in data.get("repos") or ()),
                        changes=tuple(Change.from_dict(c) for c in data.get("changes") or ()),
                        owner=Owner.from_dict(data.get("owner")),
                        channels=tuple(Channel(str(c.get("id") or ""), str(c.get("name") or ""))
                                       for c in data.get("channels") or () if isinstance(c, dict)),
                        notes=tuple(str(n) for n in data.get("notes") or ()),
                        code_checked=bool(data.get("code_checked")), draft=str(data.get("draft") or ""))
