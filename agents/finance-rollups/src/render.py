"""User-facing text, confidence and the output envelope. Every number shown is computed in code (rollup.py,
reconcile.py) and formatted here, so the narrative check builds its allow-list from the same formats."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import months as m
from reconcile import MATCH, MISMATCH, UNMAPPED, Reconciliation
from rollup import MonthRollup
from rows import InvoiceRow

NA = "n/a"
PAID_DATE_NOTE = "Invoiced paid is by paid date, not recognised revenue"
SOURCE_ACCOUNT, SOURCE_PASTED = "account", "pasted"


@dataclass(frozen=True)
class Report:
    """One computed run, kept in the conversation so follow-ups and a later ledger paste use the same numbers."""

    months: tuple[str, ...]
    rollups: tuple[MonthRollup, ...]
    reconciliation: tuple[Reconciliation, ...] = ()
    invoices: tuple[InvoiceRow, ...] = ()
    source: str = SOURCE_ACCOUNT
    livemode: bool | None = None
    invoices_known: bool = True
    remaining: int = 0               # balance rows over the cap; -1 when pages were left unread
    dropped: int = 0
    inconsistent: int = 0
    mapped: int = 0
    clamped: bool = False
    ledger_given: bool = False
    notes: tuple[str, ...] = ()
    narrative: str = ""
    narrative_checked: bool = True

    @property
    def label(self) -> str:
        return m.range_label(self.months)

    @property
    def identity_failed(self) -> bool:
        return any(not r.identity_ok for r in self.rollups)

    @property
    def partial_months(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.month for r in self.rollups if r.partial))

    def with_(self, **changes: Any) -> Report:
        return replace(self, **changes)

    def to_dict(self) -> dict:
        return {"months": list(self.months), "rollups": [r.model_dump() for r in self.rollups],
                "reconciliation": [r.model_dump(mode="json") for r in self.reconciliation],
                "invoices": [i.model_dump() for i in self.invoices], "source": self.source,
                "livemode": self.livemode, "invoices_known": self.invoices_known, "remaining": self.remaining,
                "dropped": self.dropped, "inconsistent": self.inconsistent, "mapped": self.mapped,
                "clamped": self.clamped, "ledger_given": self.ledger_given, "notes": list(self.notes),
                "narrative": self.narrative, "narrative_checked": self.narrative_checked}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Report:
        return Report(months=tuple(data.get("months") or ()),
                      rollups=tuple(MonthRollup(**r) for r in data.get("rollups") or ()),
                      reconciliation=tuple(Reconciliation(**r) for r in data.get("reconciliation") or ()),
                      invoices=tuple(InvoiceRow(**i) for i in data.get("invoices") or ()),
                      source=str(data.get("source") or SOURCE_ACCOUNT), livemode=data.get("livemode"),
                      invoices_known=bool(data.get("invoices_known", True)), remaining=int(data.get("remaining") or 0),
                      dropped=int(data.get("dropped") or 0), inconsistent=int(data.get("inconsistent") or 0),
                      mapped=int(data.get("mapped") or 0), clamped=bool(data.get("clamped")),
                      ledger_given=bool(data.get("ledger_given")), notes=tuple(data.get("notes") or ()),
                      narrative=str(data.get("narrative") or ""),
                      narrative_checked=bool(data.get("narrative_checked", True)))


@dataclass(frozen=True)
class Confidence:
    level: str
    needs_human: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


# --- number formats (the narrative allow-list uses exactly these) -------------------------------------------

def money(minor: int | None) -> str:
    if minor is None:
        return NA
    sign = "-" if minor < 0 else ""
    major, cents = divmod(abs(minor), 100)
    return f"{sign}{major:,}.{cents:02d}"


def signed_money(minor: int | None) -> str:
    if minor is None:
        return NA
    return money(minor) if minor < 0 else f"+{money(minor)}"


def pct(value: str | None) -> str:
    return NA if value is None else f"{value}%"


def signed_pct(value: str | None) -> str:
    if value is None:
        return NA
    return f"{value}%" if value.startswith("-") else f"+{value}%"


def mode_label(report: Report) -> str:
    if report.source == SOURCE_PASTED:
        return "from the rows you pasted"
    return "test mode" if report.livemode is False else ("live mode" if report.livemode else "")


# --- the reply ------------------------------------------------------------------------------------------------

def _currencies(report: Report) -> tuple[str, ...]:
    return tuple(dict.fromkeys(r.currency for r in report.rollups))


def table(report: Report, currency: str) -> str:
    rows = [r for r in report.rollups if r.currency == currency]
    head = ("| Month | Gross | Refunds | Disputes | Fees | Net | Payouts | Invoiced paid |\n"
            "|---|---:|---:|---:|---:|---:|---:|---:|")
    body = [f"| {m.label(r.month)}{' (partial)' if r.partial else ''} | {money(r.gross)} | {money(r.refunds)} | "
            f"{money(r.disputes)} | {money(r.fees)} | {money(r.net)} | {money(r.payouts)} | {money(r.invoiced_paid)} |"
            for r in rows]
    return "\n".join([head, *body])


def rates_line(r: MonthRollup) -> str:
    parts = [f"refund rate {pct(r.refund_rate)}", f"fee rate {pct(r.fee_rate)}",
             f"{r.charges} charge(s), {r.refund_count} refund(s), {r.dispute_count} dispute(s)",
             f"gross MoM {signed_pct(r.mom_gross_pct)}", f"net MoM {signed_pct(r.mom_net_pct)}"]
    if r.invoiced_paid is not None:
        parts.append(f"invoiced MoM {signed_pct(r.mom_invoiced_pct)}")
        if r.subscription_share is not None:
            parts.append(f"subscriptions {pct(r.subscription_share)} of invoiced")
    return f"- {m.label(r.month)}: " + "; ".join(parts)


def headline(report: Report, currency: str) -> str:
    mode = ", ".join(p for p in (mode_label(report), currency) if p)
    return f"Month-end rollup, {report.label} ({mode})"


def tie_out_line(item: Reconciliation) -> str:
    where = f"{m.label(item.month)} {item.item}" + (f" ({item.currency})" if item.currency else "")
    if item.status == UNMAPPED:
        return f"- Unmapped: \"{item.item}\" {money(item.ledger_amount)} ({m.label(item.month)}), not compared."
    if item.ledger_amount is None:
        return f"- {where}: no ledger line vs {money(item.payments_amount)} in the payments account: mismatch " \
               f"({_candidates(item)})."
    if item.payments_amount is None:
        return (f"- {where}: ledger {money(item.ledger_amount)}, nothing to compare it with: mismatch "
                f"({_candidates(item)}).")
    head = (f"- {where}: ledger {money(item.ledger_amount)} vs {money(item.payments_amount)}, variance "
            f"{signed_money(item.variance)} ({signed_pct(item.variance_pct)})")
    if item.status == MATCH:
        return f"{head}: ties out."
    found = _candidates(item)
    return f"{head}: mismatch. {found[:1].upper()}{found[1:]}."


def _candidates(item: Reconciliation) -> str:
    parts = []
    for c in item.candidate_explanations:
        if c.kind == "unexplained":
            parts.append("unexplained by the data")
            continue
        text = f"candidate {c.kind}"
        if c.detail:
            text += f": {c.detail}"
        if c.invoice_ids:
            text += f" ({', '.join(c.invoice_ids)})"
        if c.amount_minor is not None:
            text += f", {money(c.amount_minor)}"
        parts.append(text)
    return "; ".join(parts) or "unexplained by the data"


def notes(report: Report) -> list[str]:
    out = [PAID_DATE_NOTE, "months are UTC calendar months", "currencies are never converted"]
    if report.partial_months:
        left = "more rows" if report.remaining < 0 else f"{report.remaining:,} row(s)"
        out.append(f"partial: {', '.join(m.label(x) for x in report.partial_months)} not fully read ({left} beyond "
                   "the cap or unread pages)")
    if not report.invoices_known:
        out.append("invoices not read, so invoiced paid is n/a")
    if report.inconsistent:
        out.append(f"{report.inconsistent} row(s) whose net is not amount minus fee")
    if report.identity_failed:
        bad = ", ".join(f"{m.label(r.month)} {r.currency}" for r in report.rollups if not r.identity_ok)
        out.append(f"reconcile failure: gross - refunds - disputes - fees + other does not equal net in {bad}")
    if report.dropped:
        out.append(f"{report.dropped} record(s) left out because I could not match them to tool output")
    if report.mapped:
        out.append(f"{report.mapped} row(s) had no reporting category and were mapped from their type")
    if report.clamped:
        out.append("range clamped to the latest 12 months")
    return out


def render_report(report: Report, with_hint: bool = True) -> str:
    parts: list[str] = []
    for currency in _currencies(report):
        lines = [headline(report, currency), table(report, currency), "",
                 *[rates_line(r) for r in report.rollups if r.currency == currency]]
        parts.append("\n".join(lines))
    if report.reconciliation:
        parts.append("Ledger tie-out:\n" + "\n".join(tie_out_line(i) for i in report.reconciliation))
    parts.append("Notes: " + "; ".join(notes(report)) + ".")
    parts.extend(report.notes)
    if report.narrative:
        label = "Month-end narrative" if report.narrative_checked else "Month-end narrative (assembled by code)"
        parts.append(f"{label}: {report.narrative}")
    if with_hint and not report.ledger_given:
        parts.append("Paste ledger totals (month, account, amount, currency) and I'll tie them out month by month.")
    return "\n\n".join(parts)


# --- confidence and the envelope -----------------------------------------------------------------------------

def confidence(report: Report) -> Confidence:
    unexplained = any(i.unexplained for i in report.reconciliation)
    needs_human = unexplained or bool(report.partial_months) or report.identity_failed
    if report.identity_failed or report.source == SOURCE_PASTED:
        reason = "identity check failed" if report.identity_failed else "pasted rows only"
        return Confidence("low", needs_human, (reason,))
    unmapped = any(i.status == UNMAPPED for i in report.reconciliation)
    reasons = tuple(r for r, on in (("partial month", bool(report.partial_months)),
                                    ("inconsistent rows", report.inconsistent > 0),
                                    ("invoices not read", not report.invoices_known),
                                    ("unmapped ledger lines", unmapped)) if on)
    return Confidence("medium" if reasons else "high", needs_human, reasons)


def _major(minor: int | None) -> float | None:
    return None if minor is None else float(Decimal(minor) / 100)


def _ratio(value: str | None) -> float | None:
    return None if value is None else float(value)


def month_dict(r: MonthRollup) -> dict:
    return {"month": r.month, "currency": r.currency, "gross": _major(r.gross), "refunds": _major(r.refunds),
            "disputes": _major(r.disputes), "fees": _major(r.fees), "net": _major(r.net), "payouts": _major(r.payouts),
            "charges": r.charges, "refund_rate": _ratio(r.refund_rate), "fee_rate": _ratio(r.fee_rate),
            "invoiced_paid": _major(r.invoiced_paid), "subscription_share": _ratio(r.subscription_share),
            "mom_gross_pct": _ratio(r.mom_gross_pct), "partial": r.partial}


def reconciliation_dict(i: Reconciliation) -> dict:
    return {"month": i.month, "item": i.item, "currency": i.currency, "ledger_amount": _major(i.ledger_amount),
            "payments_amount": _major(i.payments_amount), "variance": _major(i.variance),
            "variance_pct": _ratio(i.variance_pct), "status": i.status,
            "candidate_explanations": [{"kind": c.kind, "amount": _major(c.amount_minor), "detail": c.detail,
                                        "invoice_ids": list(c.invoice_ids)} for c in i.candidate_explanations]}


def summary(report: Report) -> str:
    latest = {r.currency: r for r in report.rollups}
    figures = "; ".join(f"{c} {m.label(r.month)} gross {money(r.gross)}, net {money(r.net)}" for c, r in latest.items())
    ties = [i for i in report.reconciliation if i.status != UNMAPPED]
    tie = ""
    if ties:
        mismatches = sum(1 for i in ties if i.status == MISMATCH)
        tie = f"; ledger: {len(ties) - mismatches} of {len(ties)} item(s) tie out"
    return f"Month-end rollup, {report.label}: {figures or 'no activity'}{tie}"


def envelope(report: Report) -> dict:
    conf = confidence(report)
    months_out = [month_dict(r) for r in report.rollups]
    return {"summary": summary(report), "items": months_out, "confidence": conf.level, "needs_human": conf.needs_human,
            "months": months_out, "reconciliation": [reconciliation_dict(i) for i in report.reconciliation],
            "narrative": report.narrative, "mode": mode_label(report), "source": report.source,
            "amounts": "major units, per currency"}


def json_block(env: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"


def facts(report: Report) -> dict:
    """What the narrative is worded from: values exactly as the reply shows them."""
    latest_rows = {r.currency: r for r in report.rollups}
    ledger: dict | None = None
    ties = [i for i in report.reconciliation if i.status != UNMAPPED]
    if ties:
        worst = max((i for i in ties if i.unexplained and i.variance is not None),
                    key=lambda i: abs(i.variance or 0), default=None)
        ledger = {"items_compared": len(ties), "items_tied_out": sum(1 for i in ties if i.status == MATCH),
                  "mismatches": [{"month": m.label(i.month), "item": i.item, "variance": signed_money(i.variance),
                                  "candidates": [c.kind for c in i.candidate_explanations]}
                                 for i in ties if i.status == MISMATCH],
                  "largest_unexplained": None if worst is None else {
                      "month": m.label(worst.month), "item": worst.item, "variance": signed_money(worst.variance)}}
    return {"range": report.label, "mode": mode_label(report),
            "partial_months": [m.label(x) for x in report.partial_months],
            "latest": [{"month": m.label(r.month), "currency": c, "gross": money(r.gross), "net": money(r.net),
                        "gross_mom": signed_pct(r.mom_gross_pct), "net_mom": signed_pct(r.mom_net_pct),
                        "refund_rate": pct(r.refund_rate), "fee_rate": pct(r.fee_rate), "disputes": r.dispute_count,
                        "invoiced_paid": money(r.invoiced_paid)} for c, r in latest_rows.items()],
            "ledger": ledger}


def only_currency(report: Report, currency: str | None) -> Report:
    if currency is None:
        return report
    return report.with_(rollups=tuple(r for r in report.rollups if r.currency == currency))


def months_of(rollups: Sequence[MonthRollup]) -> tuple[str, ...]:
    return tuple(sorted({r.month for r in rollups}))


def latest_per_currency(report: Report) -> tuple[MonthRollup, ...]:
    latest_rows = {r.currency: r for r in report.rollups}
    return tuple(latest_rows.values())
