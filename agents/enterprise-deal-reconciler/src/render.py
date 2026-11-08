"""Pure: the reply (markdown or a fenced json block) and the envelope, both built in code from the checked
terms, the billing plan and the comparison report. `confidence` and `needs_human` are computed here."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from compare import Mismatch, Report
from terms import FIELDS, LABELS, ContractTerms

QUOTE_CHARS = 80
HIGH_CONFIDENCE_FIELDS = 6
LOW_CONFIDENCE_FIELDS = 3
FIELD_LABELS = {**LABELS, "net_amount_per_period": "net amount per period", "payment_terms": "payment terms",
                "subscription": "subscription", "end_date": "end date / term"}


@dataclass(frozen=True)
class RunMeta:
    customer: str | None = None
    contract_source: str = "documents"          # documents | pasted
    contract_title: str | None = None
    contract_link: str | None = None
    billing_source: str | None = None           # tool | pasted | None (not read)
    livemode: bool | None = None
    contract_complete: bool = True
    salvaged: bool = False
    billing_dropped: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def confidence(report: Report | None, terms: ContractTerms, meta: RunMeta) -> str:
    compared = report.compared if report else 0
    if compared < LOW_CONFIDENCE_FIELDS:
        return "low"
    doubts = (not meta.contract_complete or bool(terms.ambiguous) or bool(terms.dropped) or meta.salvaged
              or meta.contract_source == "pasted" or meta.billing_source == "pasted" or meta.billing_dropped > 0)
    return "high" if compared >= HIGH_CONFIDENCE_FIELDS and not doubts else "medium"


def needs_human(report: Report | None, level: str) -> bool:
    if report is None or level == "low":
        return True
    kinds = {m.kind for m in report.mismatches}
    return (any(m.severity == "critical" for m in report.mismatches) or "ambiguous_in_contract" in kinds
            or "no_subscription" in kinds)


def envelope(report: Report | None, terms: ContractTerms, meta: RunMeta) -> dict:
    level = confidence(report, terms, meta)
    mismatches = [m.to_dict() for m in report.mismatches] if report else []
    plan = report.plan if report else None
    return {
        "summary": summary_line(report, meta),
        "items": mismatches,
        "confidence": level,
        "needs_human": needs_human(report, level),
        "mismatches": mismatches,
        "verified": list(report.verified) if report else [],
        "unverified": [*(report.unverified if report else _not_stated(terms)), *_dropped_lines(terms)],
        "verdict": report.verdict if report else "review",
        "customer": meta.customer,
        "subscription_id": plan.id if plan and plan.kind != "pasted" else None,
        "first_invoice_in_days": report.first_invoice_days if report else None,
        "terms": {f: {"value": terms.terms[f].shown(), "quote": terms.terms[f].quote}
                  for f in FIELDS if f in terms.terms},
        "sources": {"contract": meta.contract_source, "contract_title": meta.contract_title,
                    "billing": meta.billing_source, "livemode": meta.livemode},
        "notes": list(meta.notes),
    }


def summary_line(report: Report | None, meta: RunMeta) -> str:
    who = meta.customer or "Contract"
    if report is None:
        return f"{who}: contract terms extracted; billing not read, so nothing was compared."
    counts = [f"{report.count(s)} {s}" for s in ("critical", "high", "medium") if report.count(s)]
    tail = ", ".join(counts) if counts else "no mismatches"
    return f"{who} vs billing{_mode(meta)}: verdict {report.verdict.upper()}, {tail}"


def _mode(meta: RunMeta) -> str:
    if meta.billing_source == "pasted":
        return " (pasted)"
    return {False: " (test mode)", True: " (live mode)"}.get(meta.livemode, "")


def render(report: Report | None, terms: ContractTerms, meta: RunMeta) -> str:
    if report is None:
        return render_terms_only(terms, meta)
    lines = [summary_line(report, meta)]
    if report.banner:
        lines = [*lines, "", f"**{report.banner}**"]
    if report.mismatches:
        lines = [*lines, "", "| Field | Contract | Billing | Severity | Fix |", "|---|---|---|---|---|",
                 *(_row(m) for m in report.mismatches)]
    lines = [*lines, "", *_footer(report, terms, meta)]
    return "\n".join(lines).strip()


def _row(m: Mismatch) -> str:
    contract = m.contract_value + (f" (\"{_short(m.quote)}\")" if m.quote else "")
    billed = m.billing_value + (f" ({m.billing_id})" if m.billing_id else "")
    fix = m.fix + (f" Delta: {m.delta}." if m.delta else "")
    cells = (FIELD_LABELS.get(m.field, m.field).capitalize(), contract, billed, m.severity, fix)
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _footer(report: Report, terms: ContractTerms, meta: RunMeta) -> list[str]:
    lines: list[str] = []
    if report.verified:
        lines = [*lines, "Verified: " + ", ".join(FIELD_LABELS.get(f, f) for f in report.verified) + "."]
    if report.unverified:
        lines = [*lines, "Not checked: " + "; ".join(report.unverified) + "."]
    dropped = _dropped_lines(terms)
    if dropped:
        lines = [*lines, "Dropped (not confirmed word for word in the contract): " + "; ".join(dropped) + "."]
    source = (f"Contract: \"{meta.contract_title}\"" if meta.contract_title else
              "Contract: pasted text" if meta.contract_source == "pasted" else "")
    if source:
        lines = [*lines, source + (f" ({meta.contract_link})" if meta.contract_link else "") + "."]
    plan = report.plan
    if plan is not None and plan.kind != "pasted":
        lines = [*lines, f"Compared with {plan.kind} {plan.id}" + (f" ({plan.link})" if plan.link else "") + "."]
    return [*lines, *meta.notes, "I'm read-only: make these fixes in billing yourself."]


def render_terms_only(terms: ContractTerms, meta: RunMeta) -> str:
    lines = [summary_line(None, meta), "", "| Term | Contract | Quote |", "|---|---|---|"]
    lines = [*lines, *(f"| {_cell(LABELS[f].capitalize())} | {_cell(terms.terms[f].shown())} | "
                       f"{_cell(_short(terms.terms[f].quote))} |" for f in FIELDS if f in terms.terms)]
    ambiguous = [f"{LABELS.get(a.field, a.field)} ({a.reason})" for a in terms.ambiguous]
    if ambiguous:
        lines = [*lines, "", "Ambiguous in the contract: " + "; ".join(ambiguous) + "."]
    return "\n".join([*lines, "", *meta.notes]).strip()


def _not_stated(terms: ContractTerms) -> list[str]:
    return [f"contract does not state {LABELS[f]}" for f in terms.not_stated()]


def _dropped_lines(terms: ContractTerms) -> list[str]:
    return [f"{LABELS.get(f, f)} not found in contract" for f in terms.dropped]


def _short(text: str | None) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= QUOTE_CHARS else flat[: QUOTE_CHARS - 3].rstrip() + "..."


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def as_json(data: dict) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n```"


def fixes(mismatches: Sequence[dict]) -> list[str]:
    return [f"- {FIELD_LABELS.get(m.get('field'), m.get('field'))}: {m.get('fix')}" for m in mismatches if m.get("fix")]
