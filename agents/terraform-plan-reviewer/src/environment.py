"""Pure: the target environment and the stated intent.

Environment: a stated value wins (`env: prod`, "Review this staging plan"); else it is detected from a
workspace line, an `Environment`/`env` tag or label, or `prod` in 3+ addresses; still unknown → prod (fail
safe), and the reply says it was assumed."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from changes import Change, known

PROD, STAGING, DEV = "prod", "staging", "dev"
_ALIASES = {"prod": PROD, "production": PROD, "prd": PROD, "live": PROD, "stage": STAGING, "staging": STAGING,
            "stg": STAGING, "uat": STAGING, "qa": STAGING, "preprod": STAGING, "dev": DEV, "development": DEV,
            "test": DEV, "testing": DEV, "sandbox": DEV, "sbx": DEV}
_LABELLED = re.compile(r"\b(?:env|environment|workspace)\s*[:=]\s*\"?([A-Za-z\-]+)", re.IGNORECASE)
_WORDS = re.compile(r"\b(prod|production|staging|stage|uat|preprod|dev|development|sandbox)\b", re.IGNORECASE)
_PROD_TOKEN = re.compile(r"(?<![a-z])(?:prod|production|prd)(?![a-z])", re.IGNORECASE)
_TAG_KEYS = ("environment", "env", "stage")
_INTENT_LABEL = re.compile(r"\bintent\s*[:=]\s*(.+?)(?:\n|$)", re.IGNORECASE)
_INTENT_PHRASE = re.compile(r"\b(?:this|the) (?:pr|change|plan|mr)\s+(?:should |is meant to |will |is to )?"
                            r"((?:add|adds|create|creates|remove|removes|update|updates|rename|renames|move|moves|"
                            r"replace|replaces|upgrade|upgrades|change|changes)\b[^\n.]*)", re.IGNORECASE)
PROD_ADDRESSES_MIN = 3
MAX_INTENT_CHARS = 300


@dataclass(frozen=True)
class Environment:
    name: str          # prod | staging | dev
    source: str        # stated | detected | assumed

    @property
    def assumed(self) -> bool:
        return self.source == "assumed"

    @property
    def is_prod(self) -> bool:
        return self.name == PROD


def normalize(value: str) -> str:
    return _ALIASES.get((value or "").strip().casefold(), "")


def stated(instruction: str, envelope_value: str = "") -> str:
    if normalize(envelope_value):
        return normalize(envelope_value)
    labelled = _LABELLED.search(instruction or "")
    if labelled and normalize(labelled.group(1)):
        return normalize(labelled.group(1))
    words = [normalize(w) for w in _WORDS.findall(instruction or "")]
    return words[0] if words else ""


def resolve(instruction: str, envelope_value: str, workspace: str, changes: Sequence[Change]) -> Environment:
    said = stated(instruction, envelope_value)
    if said:
        return Environment(said, "stated")
    detected = normalize(workspace) or _from_tags(changes) or _from_addresses(changes)
    if detected:
        return Environment(detected, "detected")
    return Environment(PROD, "assumed")


def _from_tags(changes: Sequence[Change]) -> str:
    for change in changes:
        for attr in change.attrs:
            leaf = attr.name.rsplit(".", 1)[-1].casefold()
            scope = attr.name.split(".", 1)[0]
            if scope in ("tags", "tags_all", "labels", "default_tags") and leaf in _TAG_KEYS:
                value = attr.after if known(attr.after) else attr.before
                if normalize(value or ""):
                    return normalize(value or "")
    return ""


def _from_addresses(changes: Sequence[Change]) -> str:
    hits = sum(1 for c in changes if _PROD_TOKEN.search(c.address))
    return PROD if hits >= PROD_ADDRESSES_MIN else ""


def intent(instruction: str, envelope_value: str = "") -> str:
    if envelope_value.strip():
        return envelope_value.strip()[:MAX_INTENT_CHARS]
    labelled = _INTENT_LABEL.search(instruction or "")
    if labelled:
        return labelled.group(1).strip().rstrip(".")[:MAX_INTENT_CHARS]
    phrase = _INTENT_PHRASE.search(instruction or "")
    return phrase.group(1).strip()[:MAX_INTENT_CHARS] if phrase else ""
