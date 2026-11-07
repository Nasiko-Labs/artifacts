"""Pure: which header holds the date, the service and the cost (and the optional dimensions), from the
synonyms the common cloud billing exports use. Matching is case-insensitive on a normalised name (spaces,
underscores, slashes, colons, dots and brackets removed). Each required role has two tiers: one tier-1 match
wins; with none, one tier-2 match wins; anything else is ambiguous and is asked about (HITL)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

ROLES = ("date", "service", "cost")
_NORM = re.compile(r"[\s_\-/:().\"'\[\]]+")


def norm(name: str) -> str:
    return _NORM.sub("", (name or "").strip().strip('"').casefold())


def _set(*names: str) -> frozenset[str]:
    return frozenset(norm(n) for n in names)


REQUIRED: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "date": (_set("date", "usage_date", "usagedate", "day", "usagestartdate", "line_item_usage_start_date",
                  "usage_start_time", "chargeperiodstart"),
             _set("bill_date", "billingperiodstart", "month")),
    "service": (_set("service", "servicename", "service_description", "productname", "product/productname",
                     "metercategory", "service name"),
                _set("product_servicecode", "line_item_product_code", "consumedservice")),
    "cost": (_set("cost", "unblendedcost", "line_item_unblended_cost", "billedcost", "cost_in_billing_currency"),
             _set("amount", "blendedcost", "effectivecost", "pretaxcost", "cost (usd)", "total")),
}
OPTIONAL: dict[str, frozenset[str]] = {
    "account": _set("account", "linkedaccount", "line_item_usage_account_id", "project_id", "subscriptionid",
                    "account_id", "account_name", "subscription_name", "project_name"),
    "region": _set("region", "location", "availabilityzone", "product_region", "resourcelocation"),
    "usage_type": _set("usage_type", "usagetype", "line_item_usage_type", "skuname", "sku_description", "metername"),
    "line_type": _set("line_type", "line_item_type", "line_item_line_item_type", "charge_type", "chargetype"),
    "currency": _set("currency", "billingcurrency", "line_item_currency_code", "currency_code"),
    "quantity": _set("quantity", "usage_amount", "line_item_usage_amount"),
}
TAG_PREFIXES = ("resourcetags/user:", "resource_tags_user_", "resourcetags_user_", "tags.", "tags_", "tag_",
                "tag:", "labels.", "user_")
TAG_WHOLE = frozenset({"labels", "tags"})


@dataclass(frozen=True)
class Columns:
    """Resolved header names (as written in the file). `tags` maps tag key → header."""

    date: str | None = None
    service: str | None = None
    cost: str | None = None
    account: str | None = None
    region: str | None = None
    usage_type: str | None = None
    line_type: str | None = None
    currency: str | None = None
    quantity: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)
    ambiguous: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # role → candidates (or all headers)
    guessed: tuple[str, ...] = ()

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(r for r in ROLES if getattr(self, r) is None)

    def with_role(self, role: str, header: str, guessed: bool = False) -> Columns:
        ambiguous = {k: v for k, v in self.ambiguous.items() if k != role}
        return replace(self, **{role: header}, ambiguous=ambiguous,
                       guessed=(*self.guessed, role) if guessed else self.guessed)


def tag_key(header: str) -> str | None:
    low = (header or "").strip().strip('"').casefold()
    if low in TAG_WHOLE:
        return low
    for prefix in TAG_PREFIXES:
        if low.startswith(prefix) and len(low) > len(prefix):
            return low[len(prefix):]
    return None


def detect(headers: Sequence[str], overrides: Mapping[str, str] | None = None) -> Columns:
    """Resolve every role. `overrides` (from a HITL answer) pins a role to a header."""
    overrides = dict(overrides or {})
    taken = set(overrides.values())
    resolved: dict[str, str | None] = {}
    ambiguous: dict[str, tuple[str, ...]] = {}
    for role in ROLES:
        if role in overrides:
            resolved[role] = overrides[role]
            continue
        header, candidates = _resolve(headers, *REQUIRED[role], taken)
        resolved[role] = header
        if header is None:
            ambiguous[role] = candidates or tuple(h for h in headers if h not in taken)
        else:
            taken.add(header)
    for role, names in OPTIONAL.items():
        resolved[role] = next((h for h in headers if norm(h) in names and h not in taken), None)
    tags = {}
    for h in headers:
        key = tag_key(h)
        if key and h not in taken and key not in tags:
            tags[key] = h
    return Columns(**resolved, tags=tags, ambiguous=ambiguous)


def _resolve(headers: Sequence[str], tier1: frozenset[str], tier2: frozenset[str],
             taken: set[str]) -> tuple[str | None, tuple[str, ...]]:
    free = [h for h in headers if h not in taken]
    first = [h for h in free if norm(h) in tier1]
    if len(first) == 1:
        return first[0], ()
    if first:
        return None, tuple(first)
    second = [h for h in free if norm(h) in tier2]
    if len(second) == 1:
        return second[0], ()
    return None, tuple(second)


def match_header(answer: str, headers: Sequence[str]) -> str | None:
    """The header a HITL answer names (exact, case-insensitive, or normalised)."""
    plain = (answer or "").strip().strip('"').strip("`")
    for h in headers:
        if h == plain or h.casefold() == plain.casefold() or (norm(h) and norm(h) == norm(plain)):
            return h
    return None
