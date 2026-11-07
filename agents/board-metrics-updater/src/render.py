"""User-facing text and the output envelope. Every number shown is computed in code (mrr.py) and
formatted here, so the narrative check can build its allow-list from the same formats."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from mrr import Block

NA = "n/a (no starting MRR)"
NOT_COMPUTED = "not computed"


@dataclass(frozen=True)
class Report:
    """One computed run, kept in the conversation so follow-ups and writes use the same numbers."""

    period: dict                      # period.Period.to_dict()
    blocks: tuple[Block, ...]
    livemode: bool | None = None
    approximate: bool = False
    pasted: bool = False
    pages_left_out: bool = False
    dropped: int = 0
    notes: tuple[str, ...] = ()
    narrative: str = ""
    narrative_checked: bool = True

    @property
    def label(self) -> str:
        return str(self.period.get("label") or "")

    def to_dict(self) -> dict:
        return {"period": dict(self.period), "blocks": [b.to_dict() for b in self.blocks], "livemode": self.livemode,
                "approximate": self.approximate, "pasted": self.pasted, "pages_left_out": self.pages_left_out,
                "dropped": self.dropped, "notes": list(self.notes), "narrative": self.narrative,
                "narrative_checked": self.narrative_checked}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Report:
        return Report(period=dict(data.get("period") or {}),
                      blocks=tuple(Block.from_dict(b) for b in data.get("blocks") or ()),
                      livemode=data.get("livemode"), approximate=bool(data.get("approximate")),
                      pasted=bool(data.get("pasted")), pages_left_out=bool(data.get("pages_left_out")),
                      dropped=int(data.get("dropped") or 0), notes=tuple(str(n) for n in data.get("notes") or ()),
                      narrative=str(data.get("narrative") or ""),
                      narrative_checked=bool(data.get("narrative_checked", True)))


@dataclass(frozen=True)
class Confidence:
    level: str
    needs_human: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


# --- number formats (the narrative allow-list uses exactly these) -------------------------------------

def money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    major, cents = divmod(abs(minor), 100)
    return f"{sign}{major:,}.{cents:02d}"


def signed_money(minor: int) -> str:
    return money(minor) if minor < 0 else f"+{money(minor)}"


def plain_money(minor: int) -> str:
    """For a sheet cell: no thousands separator."""
    return money(minor).replace(",", "")


def pct(value: Decimal | None) -> str:
    return NA if value is None else f"{value}%"


def signed_pct(value: Decimal | None) -> str:
    if value is None:
        return NA
    return f"{value}%" if value < 0 else f"+{value}%"


def maybe(block: Block, minor: int) -> str:
    return money(minor) if block.changes_known else NOT_COMPUTED


# --- the reply --------------------------------------------------------------------------------------------

def mode_label(report: Report) -> str:
    if report.pasted:
        return "from the rows you pasted"
    return "test mode" if report.livemode is False else ("live mode" if report.livemode else "")


def headline(block: Block, report: Report) -> str:
    mode = ", ".join(p for p in (mode_label(report), block.currency) if p)
    return f"Board metrics, {report.label} ({mode})"


def figures(block: Block) -> str:
    return (f"MRR {money(block.mrr1)} ({signed_money(block.net_new)}, {signed_pct(block.mom_pct)}) | "
            f"ARR {money(block.arr)} | NRR {pct(block.nrr_pct)} | GRR {pct(block.grr_pct)} | "
            f"Logo churn {pct(block.logo_churn_pct)} | Revenue churn {pct(block.revenue_churn_pct)} | "
            f"Customers {block.customers1}")


def bridge(block: Block) -> str:
    line = (f"Bridge: start {money(block.mrr0)} + new {money(block.new_mrr)} + expansion "
            f"{maybe(block, block.expansion_mrr)} - contraction {maybe(block, block.contraction_mrr)} - churned "
            f"{money(block.churned_mrr)} = {money(block.mrr1)}")
    if block.reconcile_diff:
        line += f" (does not reconcile by {money(block.reconcile_diff)})"
    return line


def block_notes(block: Block, report: Report) -> list[str]:
    notes = []
    if block.reactivation_mrr:
        notes.append(f"new includes {money(block.reactivation_mrr)} from returning customers")
    if block.unsupported_items:
        notes.append(f"{block.unsupported_items} metered or tiered item(s) excluded")
    if block.trialing_mrr:
        notes.append(f"trialing {money(block.trialing_mrr)} not in MRR")
    if block.past_due_mrr:
        notes.append(f"past due {money(block.past_due_mrr)} counted in MRR")
    if block.intra_period_churn_count:
        notes.append(f"{block.intra_period_churn_count} customer(s) joined and left inside the period")
    if block.amount_off_ignored:
        notes.append("amount discounts on non-monthly plans ignored")
    if not block.changes_known:
        notes.append("expansion and contraction not computed (no plan-change history)")
    return notes


def report_notes(report: Report) -> list[str]:
    notes = [f"plan changes read for the last 30 days only (approximate: {'yes' if report.approximate else 'no'})"]
    if report.pasted:
        notes = []
    if report.pages_left_out:
        notes.append("some subscription pages were not read, so totals may be low")
    if report.dropped:
        notes.append(f"{report.dropped} record(s) left out because I could not match them to tool output")
    if report.period.get("clamped"):
        notes.append("period clamped to 13 months")
    notes.append("periods are UTC calendar dates")
    return notes


def render_report(report: Report, has_sheet_hint: bool = True) -> str:
    parts = []
    for block in report.blocks:
        lines = [headline(block, report), figures(block), bridge(block)]
        notes = block_notes(block, report)
        if notes:
            lines.append("Notes: " + "; ".join(notes) + ".")
        parts.append("\n".join(lines))
    parts.append("Notes: " + "; ".join(report_notes(report)) + ".")
    parts.extend(report.notes)
    if report.narrative:
        label = "Paragraph (draft)" if report.narrative_checked else "Paragraph (draft, assembled by code)"
        parts.append(f"{label}: \"{report.narrative}\"")
    if has_sheet_hint:
        parts.append('Say "add it to <sheet name>" to append the row, or "add the paragraph to <doc name>".')
    return "\n\n".join(parts)


# --- confidence and the envelope ----------------------------------------------------------------------------

def confidence(report: Report) -> Confidence:
    reconcile = any(b.reconcile_diff for b in report.blocks)
    items = sum(b.total_items for b in report.blocks)
    unsupported = sum(b.unsupported_items for b in report.blocks)
    needs_human = reconcile or report.pages_left_out or (items > 0 and unsupported * 10 > items)
    if report.pasted or reconcile:
        return Confidence("low", needs_human, ("pasted rows" if report.pasted else "bridge does not reconcile",))
    reasons = tuple(r for r, on in (("approximate plan history", report.approximate),
                                    ("pages left out", report.pages_left_out),
                                    ("unsupported items", unsupported > 0),
                                    ("plan changes not read", any(not b.changes_known for b in report.blocks)))
                    if on)
    return Confidence("medium" if reasons else "high", needs_human, reasons)


def metrics_dict(block: Block, report: Report) -> dict:
    def ratio(value: Decimal | None) -> float | None:
        return None if value is None else float(value)

    def major(minor: int) -> float:
        return float(Decimal(minor) / 100)

    return {"period": report.label, "currency": block.currency, "mrr": major(block.mrr1), "arr": major(block.arr),
            "start_mrr": major(block.mrr0), "new_mrr": major(block.new_mrr),
            "expansion_mrr": major(block.expansion_mrr) if block.changes_known else None,
            "contraction_mrr": major(block.contraction_mrr) if block.changes_known else None,
            "churned_mrr": major(block.churned_mrr), "logo_churn_pct": ratio(block.logo_churn_pct),
            "revenue_churn_pct": ratio(block.revenue_churn_pct), "nrr_pct": ratio(block.nrr_pct),
            "grr_pct": ratio(block.grr_pct), "customers": block.customers1, "approximate": report.approximate,
            "unsupported_items": block.unsupported_items, "trialing_mrr": major(block.trialing_mrr),
            "past_due_mrr": major(block.past_due_mrr)}


def envelope(report: Report, sheet_rows: Sequence[Sequence[str]] = ()) -> dict:
    conf = confidence(report)
    items = [metrics_dict(b, report) for b in report.blocks]
    summary = "; ".join(f"{b.currency} MRR {money(b.mrr1)}, NRR {pct(b.nrr_pct)}" for b in report.blocks)
    return {"summary": f"Board metrics, {report.label}: {summary}" if summary else f"No MRR in {report.label}",
            "items": items, "metrics": items[0] if items else None, "confidence": conf.level,
            "needs_human": conf.needs_human, "sheet_row": [list(r) for r in sheet_rows],
            "narrative": report.narrative, "mode": mode_label(report)}


def json_block(env: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"


def csv_line(values: Sequence[str]) -> str:
    return ",".join(f'"{v}"' if "," in v or " " in v else v for v in values)
