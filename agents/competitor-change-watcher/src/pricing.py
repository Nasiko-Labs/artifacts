"""Pure: plans and prices from pricing text, and the facts that changed between two versions of it.

A price line names a plan and carries a currency amount (`$`, `€`, `£`) with an optional period/unit suffix
(`/mo`, `/month`, `/seat`, `/user`, `/year`, `per seat`, ...). Other numbers on the line ("10 projects") are
limits. Every delta is computed here; nothing is estimated."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

from records import PriceFact

_PRICE = re.compile(r"([$€£])\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
                    r"((?:\s*(?:/|per|a)\s*(?:mo(?:nth)?|seat|user|member|yr|year|annum)\b)*)", re.IGNORECASE)
_LIMIT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|gb|tb|mb)?\s+([a-z][a-z-]{2,})", re.IGNORECASE)
_LEAD = re.compile(r"^[\s>*#|•+-]+")
_NAME_CUT = re.compile(r"\s*(?:[:|–—(]|\s-\s|\bat\b|\bfor\b|\bcosts?\b)\s*", re.IGNORECASE)
_UNIT_WORDS = {"month": "mo", "mo": "mo", "seat": "seat", "user": "user", "member": "member", "year": "year",
               "yr": "year", "annum": "year"}
MAX_PLAN_CHARS = 40


@dataclass(frozen=True)
class Plan:
    name: str
    currency: str
    amount: float
    price: str                          # normalised, e.g. "$20/seat/mo"
    limits: MappingProxyType            # unit word → number as written
    line: str


def is_price_line(line: str) -> bool:
    return bool(_PRICE.search(line or ""))


def _suffix(raw: str) -> str:
    units = re.findall(r"mo(?:nth)?|seat|user|member|yr|year|annum", raw or "", re.IGNORECASE)
    return "".join(f"/{_UNIT_WORDS[u.casefold()]}" for u in units)


def parse_line(line: str) -> Plan | None:
    price = _PRICE.search(line or "")
    if price is None:
        return None
    head = _LEAD.sub("", line[:price.start()]).strip().rstrip(" -–—:|=")
    name = _NAME_CUT.split(head, maxsplit=1)[0].strip(" *_`")
    if not name or len(name) > MAX_PLAN_CHARS or not re.search(r"[A-Za-z]", name):
        return None
    amount = float(price.group(2).replace(",", ""))
    shown = f"{price.group(1)}{price.group(2)}{_suffix(price.group(3))}"
    rest = line[:price.start()] + " " + line[price.end():]
    limits = {m.group(3).casefold(): f"{m.group(1)}{m.group(2) or ''}" for m in _LIMIT.finditer(rest)}
    return Plan(name, price.group(1), amount, shown, MappingProxyType(limits), line.strip())


def plans(text: str) -> dict[str, Plan]:
    """Plan key (casefolded name) → plan; the first line naming a plan wins."""
    found: dict[str, Plan] = {}
    for line in str(text or "").splitlines():
        plan = parse_line(line)
        if plan is not None:
            found.setdefault(plan.name.casefold(), plan)
    return found


def delta_pct(old: float, new: float) -> float | None:
    return round((new - old) / old * 100, 1) if old > 0 else None


def _limit_facts(old: Plan, new: Plan) -> list[PriceFact]:
    facts = []
    for unit in sorted(set(old.limits) | set(new.limits)):
        before, after = old.limits.get(unit), new.limits.get(unit)
        if before != after:
            facts.append(PriceFact(plan=new.name, kind="limit", old=f"{before} {unit}" if before else None,
                                   new=f"{after} {unit}" if after else None, line=new.line))
    return facts


def diff(before: str, after: str) -> list[PriceFact]:
    old, new = plans(before), plans(after)
    facts: list[PriceFact] = []
    for key, plan in new.items():
        prior = old.get(key)
        if prior is None:
            facts.append(PriceFact(plan=plan.name, kind="added", new=plan.price, line=plan.line))
            continue
        if prior.price != plan.price:
            same = prior.currency == plan.currency
            facts.append(PriceFact(plan=plan.name, kind="changed", old=prior.price, new=plan.price,
                                   delta_pct=delta_pct(prior.amount, plan.amount) if same else None, line=plan.line))
        facts.extend(_limit_facts(prior, plan))
    facts.extend(PriceFact(plan=p.name, kind="removed", old=p.price, line=p.line)
                 for k, p in old.items() if k not in new)
    return facts
