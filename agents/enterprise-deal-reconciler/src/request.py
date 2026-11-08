"""Pure: what the user asked for, parsed in code (no LLM call).

    "Reconcile the Globex order form with billing"        → document "Globex order form", customer "Globex"
    "Check \"Acme MSA 2026\" for customer Acme Corp"         → document "Acme MSA 2026", customer "Acme Corp"
    "Contract: <pasted terms>\\nBilling:\\nquantity: 200 …"   → pasted contract text + pasted billing fields
    {"customer": "Globex", "mismatches": [...]}             → an earlier envelope: re-check that customer
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import billing

MAX_PASTE_CHARS = 100_000
_JSON_MODE = re.compile(r"\b(?:as json|json only|in json|json output)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“”']([^\"“”']{3,120})[\"“”']")
_DOC_NOUN = r"(?:contract|order form|agreement|msa|sow|deal|quote|renewal)"
_THE_DOC = re.compile(rf"\b(?:the|our|my)\s+((?:[A-Z0-9][\w&.\-]*\s+){{1,4}}?){_DOC_NOUN}\b", re.IGNORECASE)
_DOC_FOR = re.compile(rf"\b(?i:{_DOC_NOUN})\s+for\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){{0,3}})")
_CUSTOMER = re.compile(r"\b(?i:customer)\s*[:=]?\s*([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PASTE_LABEL = re.compile(r"(?:^|\n|\b)(?:contract(?: terms)?|order form|terms|agreement)\s*:\s*", re.IGNORECASE)
# A line that is only a short label ("Billing:") followed by billing `key: value` lines starts the billing side.
_SECTION_HEADER = re.compile(r"^\s*[A-Za-z][\w ]{0,30}:\s*$")
_SKIP_WORDS = frozenset({"signed", "new", "latest", "current", "final", "enterprise", "annual", "this", "that"})
_NOT_NAMES = frozenset({"the", "billing", "payments", "it", "our", "my"})


@dataclass(frozen=True)
class Request:
    document: str | None = None
    customer: str | None = None
    email: str | None = None
    contract_text: str | None = None         # pasted contract terms
    billing_fields: Mapping[str, str] = field(default_factory=dict)
    json_mode: bool = False
    envelope: bool = False

    @property
    def has_target(self) -> bool:
        return bool(self.document or self.customer or self.email or self.contract_text)

    def to_state(self) -> dict:
        return {"document": self.document, "customer": self.customer, "email": self.email,
                "contract_text": self.contract_text, "billing_fields": dict(self.billing_fields),
                "json_mode": self.json_mode, "envelope": self.envelope}

    @staticmethod
    def from_state(data: Mapping[str, Any] | None) -> Request:
        d = data if isinstance(data, Mapping) else {}
        return Request(document=d.get("document") or None, customer=d.get("customer") or None,
                       email=d.get("email") or None, contract_text=d.get("contract_text") or None,
                       billing_fields={str(k): str(v) for k, v in (d.get("billing_fields") or {}).items()},
                       json_mode=bool(d.get("json_mode")), envelope=bool(d.get("envelope")))


def wants_json(text: str) -> bool:
    return bool(_JSON_MODE.search(text or ""))


def parse(text: str) -> Request:
    message = (text or "")[:MAX_PASTE_CHARS]
    envelope = _envelope(message)
    if envelope is not None:
        return envelope
    body, billing_part = _split(message)
    contract = _pasted_contract(body)
    fields = billing.billing_lines(billing_part) if billing_part else (
        billing.billing_lines(body) if contract is None else {})
    head = body if contract is None else body[: _PASTE_LABEL.search(body).start()] or body
    document = _document(head)
    customer = _customer(head, document)
    email_match = _EMAIL.search(head)
    return Request(document=document, customer=customer, email=email_match.group(0) if email_match else None,
                   contract_text=contract, billing_fields=fields, json_mode=wants_json(message))


def _envelope(message: str) -> Request | None:
    body = message.strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, Mapping) or not ({"customer", "mismatches"} & set(data)):
        return None
    customer = data.get("customer")
    name = customer.get("name") if isinstance(customer, Mapping) else customer
    document = data.get("document")
    return Request(document=str(document) if isinstance(document, str) and document else None,
                   customer=str(name) if isinstance(name, str) and name else None, json_mode=True, envelope=True)


def _split(message: str) -> tuple[str, str]:
    """(everything before the billing section, the billing section) — the second empty when there is none."""
    lines = message.splitlines()
    for idx, line in enumerate(lines[:-1]):
        if _SECTION_HEADER.match(line) and billing.is_key_line(lines[idx + 1]):
            return "\n".join(lines[:idx]), "\n".join(lines[idx + 1:])
    return message, ""


def _pasted_contract(body: str) -> str | None:
    label = _PASTE_LABEL.search(body)
    if label is None:
        return None
    text = body[label.end():].strip()
    return text if len(text) >= 10 and any(ch.isdigit() for ch in text) else None


def _document(text: str) -> str | None:
    quoted = _QUOTED.search(text)
    if quoted:
        return quoted.group(1).strip()
    match = _THE_DOC.search(text)
    if match is None:
        return None
    noun = re.search(_DOC_NOUN, text[match.end(1):match.end()], re.IGNORECASE).group(0)
    words = [w for w in match.group(1).split() if w.casefold() not in _SKIP_WORDS]
    return " ".join([*words, noun]) if words else None


def _customer(text: str, document: str | None) -> str | None:
    for pattern in (_CUSTOMER, _DOC_FOR):
        match = pattern.search(text)
        if match and match.group(1).split()[0].casefold() not in _NOT_NAMES:
            return _trim(match.group(1))
    if document:
        words = [w for w in document.split() if not re.fullmatch(_DOC_NOUN, w, re.IGNORECASE)
                 and not re.fullmatch(r"order|form|\d{4}", w, re.IGNORECASE)]
        return " ".join(words) or None
    return None


def _trim(name: str) -> str:
    words = name.split()
    while words and words[-1].casefold() in {"with", "and", "in", "on", "against", "billing"}:
        words = words[:-1]
    return " ".join(words).strip(".,") or name
