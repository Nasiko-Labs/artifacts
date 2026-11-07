"""Pure: CRM accounts. Domains normalised, ARR parsed in code from the raw amount a tool returned,
the model's account list checked against tool output, and each call matched to one account by the
exact web domains of its external participants."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from config import Config
from records import Account, Call, text_of
from windows import parse_day

_AMOUNT = re.compile(r"^\s*(?:[A-Z]{3}\s*)?[$€£¥]?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmMbB])?\s*(?:[A-Z]{3})?\s*$")
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}


def normalise_domain(raw: str) -> str:
    text = (raw or "").strip().casefold()
    text = re.sub(r"^[a-z]+://", "", text).split("/", 1)[0].split("@")[-1]
    return text.removeprefix("www.").rstrip(".")


def parse_arr(raw: str) -> float | None:
    """`$1.2M` → 1_200_000, `120,000` → 120000, `85k` → 85000; anything else → None."""
    match = _AMOUNT.match(raw or "")
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return number * _SUFFIX.get((match.group(2) or "").casefold(), 1.0)


def _seen(value: str, seen_text: str) -> bool:
    return bool(value) and value.casefold() in seen_text.casefold()


def checked_accounts(raw: Sequence[object], seen_text: str) -> tuple[Account, ...]:
    """Accounts whose id, name and domain all appear in tool output. ARR only when its raw string does;
    a url only when it does; an unparseable renewal date becomes unknown."""
    kept: dict[str, Account] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        company_id, name = text_of(entry.get("company_id")), text_of(entry.get("name"))
        domain = normalise_domain(text_of(entry.get("domain")))
        if not (_seen(company_id, seen_text) and _seen(name, seen_text) and _seen(domain, seen_text)):
            continue
        arr_raw, url = text_of(entry.get("arr_raw")), text_of(entry.get("url"))
        renewal = parse_day(text_of(entry.get("renewal_date")) or None)
        kept.setdefault(company_id, Account(
            company_id=company_id, name=name, domain=domain, url=url if _seen(url, seen_text) else "",
            arr=parse_arr(arr_raw) if _seen(arr_raw, seen_text) else None,
            stage=text_of(entry.get("deal_stage")), renewal_date=renewal.isoformat() if renewal else None))
    return tuple(kept.values())


@dataclass(frozen=True)
class Match:
    account_id: str | None
    reason: str = ""  # "", "no account", "ambiguous", "free mail only"


def match_call(call: Call, accounts: Sequence[Account], config: Config) -> Match:
    """The account whose domain the most external participants share; a tie is ambiguous."""
    by_domain = {a.domain: a for a in accounts}
    domains = [normalise_domain(p.email) for p in call.participants if p.external and "@" in p.email]
    usable = [d for d in domains if d not in config.free_mail and d not in config.own_domains]
    if domains and not usable:
        return Match(None, "free mail only")
    counts = Counter(by_domain[d].company_id for d in usable if d in by_domain)
    if not counts:
        return Match(None, "no account")
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return Match(None, "ambiguous")
    return Match(ranked[0][0])


def lookup_domains(calls: Sequence[Call], config: Config) -> tuple[str, ...]:
    """The distinct external, non-free-mail domains to look up, most frequent first, capped."""
    counts = Counter(normalise_domain(d) for c in calls for d in c.external_domains)
    usable = [d for d, _ in counts.most_common() if d and d not in config.free_mail and d not in config.own_domains]
    return tuple(usable[:config.max_domains])
