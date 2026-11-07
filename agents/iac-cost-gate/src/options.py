"""Pure: the settings one instruction line can carry (limit, region, environment, NAT data volume,
required tags), parsed in code. A follow-up such as "set limit $300" changes only what it names."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import prices as p

_AMOUNT = r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?"
_LIMIT = re.compile(r"\b(?:limit|max(?:imum)?|budget|cap|ceiling|fail (?:above|over|if over)|under)\s*"
                    rf"(?:is|of|=|:|at|to)?\s*{_AMOUNT}", re.IGNORECASE)
_PER_MONTH = re.compile(rf"{_AMOUNT}\s*(?:/|per|a)\s*(?:month|mo)\b", re.IGNORECASE)
_NO_LIMIT = re.compile(r"\b(?:no|remove(?: the)?|drop(?: the)?|clear(?: the)?|without(?: a)?) limit\b", re.IGNORECASE)
_REGION = re.compile(r"\b((?:us|eu|ap|ca|sa|me|af|il|mx)-(?:north|south|east|west|central|northeast|southeast|"
                     r"northwest|southwest)-\d)\b", re.IGNORECASE)
_ENV_WORDS = r"(prod|production|dev|development|test|testing|staging|stage|sandbox|qa|non-?prod|uat|preprod)"
_ENV = re.compile(rf"\b(?:env(?:ironment)?\s*(?:is|=|:)?\s*|this is (?:a |the |for )?|it'?s (?:a |for )?)"
                  rf"{_ENV_WORDS}\b", re.IGNORECASE)
_NON_PROD = re.compile(r"\bnon-?prod\b", re.IGNORECASE)
_NAT_GB = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(gb|tb)\b[^.\n]{0,40}?\bnat\b|\bnat\b[^.\n]{0,40}?(\d[\d,]*(?:\.\d+)?)"
                     r"\s*(gb|tb)\b", re.IGNORECASE)
_TAGS = re.compile(r"\brequired tags?\s*(?:are|:|=)?\s*([\w:.\-]+(?:\s*(?:,|and)\s*[\w:.\-]+)*)", re.IGNORECASE)
_DELIM = re.compile(r"\s*(?:,|\band\b)\s*")


def defaults() -> dict:
    return {"limit": None, "region": "", "env": "", "nat_gb": p.NAT_DEFAULT_GB, "tag_groups": None}


def _amount(number: str, k: str | None) -> float:
    value = float(number.replace(",", ""))
    return value * 1000 if k else value


def parse(text: str) -> dict:
    """Only the settings the text names (keys absent otherwise); "no limit" sets limit None."""
    found: dict[str, Any] = {}
    plain = text or ""
    if _NO_LIMIT.search(plain):
        found["limit"] = None
    else:
        match = _LIMIT.search(plain) or _PER_MONTH.search(plain)
        if match:
            found["limit"] = _amount(match.group(1), match.group(2))
    region = _REGION.search(plain)
    if region:
        found["region"] = region.group(1).casefold()
    env = _ENV.search(plain)
    if env:
        found["env"] = "prod" if env.group(1).casefold() == "production" else env.group(1).casefold()
    elif _NON_PROD.search(plain):
        found["env"] = "non-prod"
    nat = _NAT_GB.search(plain)
    if nat:
        number, unit = (nat.group(1), nat.group(2)) if nat.group(1) else (nat.group(3), nat.group(4))
        found["nat_gb"] = float(number.replace(",", "")) * (1024 if unit.casefold() == "tb" else 1)
    tags = _TAGS.search(plain)
    if tags:
        found["tag_groups"] = [[t] for t in _DELIM.split(tags.group(1).strip()) if t]
    return found


def merge(base: Mapping[str, Any] | None, changes: Mapping[str, Any]) -> dict:
    return {**defaults(), **(base or {}), **changes}
