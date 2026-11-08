"""Pure: from parsed resources to the rows of idle findings, the not-evaluated counts, the kept-by-tag
count and the costs left out of totals. Owners from tags are set here; owner lookup and grouping are in
owners.py. Everything is computed in code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date
from decimal import Decimal
from typing import Any

import rules
from inventory import Parsed, Resource
from request import Request

KEEP_WORDS = frozenset({"keep", "do-not-delete", "do_not_delete", "donotdelete", "do not delete", "retain"})
OWNER_TAGS = ("owner", "team", "created_by", "contact", "project")  # priority order
UNKNOWN = "unknown"
_FALSEY = frozenset({"false", "no", "0", "n"})


@dataclass(frozen=True)
class Row:
    """One idle resource as shown. `id` is the list number; costs are Decimal strings or ""."""

    id: str
    resource_id: str
    name: str
    kind: str
    detail: str
    region: str
    rule: str
    confidence: str
    idle_days: int
    idle_basis: str
    monthly_cost: str
    cost_basis: str
    currency: str
    owner: str = ""             # who, as the tag / tool / user gave it; "" = unknown
    owner_key: str = UNKNOWN    # case-folded grouping key
    owner_source: str = UNKNOWN  # "tag owner", "tag team", "code host commit", "code host search", "chat", "typed"
    email: str = ""
    chat_id: str = ""           # a chat user id or handle, only from a chat tool or typed by the user
    code_handle: str = ""       # a code-host login, from a code-host tool (shown, never a send target)
    evidence_link: str = ""
    evidence_quote: str = ""

    @property
    def cost(self) -> Decimal | None:
        return Decimal(self.monthly_cost) if self.monthly_cost else None

    @property
    def known_owner(self) -> bool:
        return self.owner_key != UNKNOWN

    @property
    def delivery(self) -> str:
        return "chat" if self.chat_id else "email" if self.email else "none"

    @property
    def estimated(self) -> bool:
        return self.cost_basis not in ("reported", "unknown")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Row:
        known = {f.name for f in fields(Row)}
        values = {k: v for k, v in data.items() if k in known}
        values["idle_days"] = int(values.get("idle_days") or 0)  # protobuf Structs return floats
        return Row(**{k: (v if k == "idle_days" else str(v or "")) for k, v in values.items()})


@dataclass(frozen=True)
class Analysis:
    rows: tuple[Row, ...]
    kept_by_tag: int = 0
    not_evaluated: Mapping[str, Mapping[str, int]] = field(default_factory=dict)  # rule → missing field → count
    below_min_cost: int = 0
    resources: int = 0
    unreadable: int = 0
    total_rows: int = 0
    over_limit: int = 0


def is_kept(r: Resource) -> bool:
    """Tagged keep / do-not-delete / retain, as a key or a value, any case (`keep=false` is not a keep)."""
    for key, value in r.tags:
        k, v = key.strip().casefold(), value.strip().casefold()
        if k in KEEP_WORDS and v not in _FALSEY:
            return True
        if v in KEEP_WORDS:
            return True
    return False


def tag_owner(r: Resource) -> tuple[str, str]:
    """(owner value, which tag) by tag priority, or ("", "")."""
    tags = r.tag_values()
    return next(((tags[k], k) for k in OWNER_TAGS if tags.get(k)), ("", ""))


def analyse(parsed: Parsed, today: date, req: Request) -> Analysis:
    volumes = {r.id: r for r in parsed.resources if r.kind == "volume"}
    rows: tuple[Row, ...] = ()
    kept = below = 0
    missing: dict[str, dict[str, int]] = {}
    for resource in parsed.resources:
        verdict = rules.evaluate(resource, today, req.older_than_days, volumes)
        if verdict is None:
            continue
        if verdict.finding is None:
            counts = missing.get(verdict.rule, {})
            missing = {**missing, verdict.rule: {**counts, verdict.missing: counts.get(verdict.missing, 0) + 1}}
            continue
        if is_kept(resource):
            kept += 1
            continue
        finding = verdict.finding
        if req.min_cost is not None and finding.monthly_cost is not None and finding.monthly_cost < req.min_cost:
            below += 1
            continue
        rows = (*rows, _row(resource, finding))
    return Analysis(rows, kept, missing, below, len(parsed.resources), parsed.unreadable, parsed.total_rows,
                    parsed.over_limit)


def _row(resource: Resource, finding: rules.Finding) -> Row:
    row = Row(id="", resource_id=resource.id, name=resource.name, kind=finding.kind, detail=finding.detail,
              region=resource.region, rule=finding.rule, confidence=finding.confidence, idle_days=finding.idle_days,
              idle_basis=finding.idle_basis,
              monthly_cost=str(finding.monthly_cost) if finding.monthly_cost is not None else "",
              cost_basis=finding.cost_basis, currency=finding.currency)
    value, tag = tag_owner(resource)
    return with_owner(row, value, f"tag {tag}") if value else row


def with_owner(row: Row, value: str, source: str, chat_id: str = "") -> Row:
    """Set the owner from a tag value or a typed contact: an email-looking value is the email."""
    is_email = "@" in value and "." in value.rsplit("@", 1)[-1] and " " not in value
    return replace(row, owner=value, owner_key=value.casefold(), owner_source=source,
                   email=value if is_email else row.email, chat_id=chat_id or row.chat_id)


def to_state(rows: Sequence[Row]) -> list[dict]:
    return [r.to_dict() for r in rows]


def from_state(data: Sequence[Mapping[str, Any]] | None) -> tuple[Row, ...]:
    return tuple(Row.from_dict(d) for d in data or ())
