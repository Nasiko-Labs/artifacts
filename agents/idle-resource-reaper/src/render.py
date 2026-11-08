"""Pure: the list reply (markdown, rendered in code), the plain listing the follow-up model reads, the
envelope (summary, items, confidence, needs_human + this agent's fields), and the copyable text of a
message that cannot be sent. Every number shown here was computed in code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

import pricing
import rules
from findings import Row
from owners import Group, regroup

MAX_ROWS_SHOWN = 40
NEEDS_HUMAN_UNKNOWN_OWNER_TOTAL = Decimal("100.00")
LOW_CONFIDENCE_UNREADABLE_SHARE = 0.20
_FIELD_WORDS = {"cpu_avg_14d": "no cpu data", "created": "no creation date", "state": "no state",
                "source_exists": "source volume not known", "association": "association not given",
                "request_count_14d": "no request or target data", "network_bytes_14d": "no network data",
                "stopped since": "no stop date", "attached volumes": "attached storage not given"}


@dataclass(frozen=True)
class ListMeta:
    resources: int = 0
    unreadable: int = 0
    total_rows: int = 0
    over_limit: int = 0
    kept_by_tag: int = 0
    below_min_cost: int = 0
    min_cost: str = ""
    owner_filter: str = ""
    not_evaluated: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    lookup_done: bool = False
    grace_days: int = 7

    def to_dict(self) -> dict:
        data = asdict(self)
        data["not_evaluated"] = {k: dict(v) for k, v in self.not_evaluated.items()}
        data["notes"] = list(self.notes)
        return data

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> ListMeta:
        data = dict(data or {})
        ints = ("resources", "unreadable", "total_rows", "over_limit", "kept_by_tag", "below_min_cost", "grace_days")
        values = {k: int(data[k]) for k in ints if data.get(k) is not None}
        nested = {str(r): {str(f): int(n) for f, n in (v or {}).items()} for r, v in (data.get("not_evaluated") or {}).items()}
        return ListMeta(**values, min_cost=str(data.get("min_cost") or ""), owner_filter=str(data.get("owner_filter") or ""),
                        not_evaluated=nested, notes=tuple(str(n) for n in data.get("notes") or ()),
                        lookup_done=bool(data.get("lookup_done")))


# --- computed facts ------------------------------------------------------------------------------

def totals(rows: Sequence[Row]) -> dict[str, Decimal]:
    """Known monthly cost per currency (unknown costs are left out)."""
    out: dict[str, Decimal] = {}
    for r in rows:
        if r.cost is not None:
            out = {**out, r.currency: out.get(r.currency, Decimal(0)) + r.cost}
    return out


def main_total(rows: Sequence[Row]) -> tuple[str, Decimal]:
    found = totals(rows)
    currency = pricing.CURRENCY if pricing.CURRENCY in found or not found else next(iter(found))
    return currency, found.get(currency, Decimal(0))


def confidence(rows: Sequence[Row], meta: ListMeta) -> str:
    if meta.total_rows and meta.unreadable / meta.total_rows > LOW_CONFIDENCE_UNREADABLE_SHARE:
        return "low"
    degraded = (any(r.estimated or r.cost is None for r in rows) or bool(meta.not_evaluated)
                or any(r.confidence != "high" for r in rows))
    return "medium" if degraded else "high"


def needs_human(rows: Sequence[Row]) -> bool:
    unknown_total = sum((r.cost for r in rows if not r.known_owner and r.cost is not None), Decimal(0))
    return (unknown_total >= NEEDS_HUMAN_UNKNOWN_OWNER_TOTAL or any(r.estimated for r in rows)
            or any(r.kind == "idle_instance" and r.confidence == "medium" for r in rows))


def _totals_text(rows: Sequence[Row]) -> str:
    found = totals(rows)
    if not found:
        return "no known cost"
    parts = [f"{pricing.money(v)} {c}" for c, v in found.items()]
    return " + ".join(parts) + "/month"


# --- markdown ------------------------------------------------------------------------------------

def kind_cell(r: Row) -> str:
    return f"{rules.LABELS.get(r.kind, r.kind)} ({r.detail})" if r.detail else rules.LABELS.get(r.kind, r.kind)


def idle_cell(r: Row) -> str:
    if r.idle_basis == "metrics window":
        return f"{r.idle_days} d window"
    return f"{r.idle_days} d" + (" (from creation)" if r.idle_basis == "creation" and r.kind in
                                 ("unattached_volume", "stopped_instance_with_storage") else "")


def cost_cell(r: Row) -> str:
    if r.cost is None:
        return "unknown"
    suffix = " (est.)" if r.estimated else ""
    currency = f" {r.currency}" if r.currency != pricing.CURRENCY else ""
    return f"{pricing.money(r.cost)}{currency}{suffix}"


def owner_cell(r: Row) -> str:
    if not r.known_owner:
        return "unknown"
    who = r.owner if not r.code_handle or r.code_handle in r.owner else f"{r.owner} (@{r.code_handle})"
    return f"{who} ({r.owner_source})"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def header_line(rows: Sequence[Row], groups: Sequence[Group]) -> str:
    estimated = sum(1 for r in rows if r.estimated)
    known_owners = sum(1 for g in groups if g.known)
    unknown = sum(1 for r in rows if not r.known_owner)
    currency = main_total(rows)[0]
    est = f" (estimated for {estimated})" if estimated else ""
    return (f"Idle resources ({currency}): {len(rows)} found, about {_totals_text(rows)}{est}, "
            f"{known_owners} owner{'s' if known_owners != 1 else ''}, {unknown} unknown")


def render_list(rows: Sequence[Row], groups: Sequence[Group], meta: ListMeta,
                outcomes: Mapping[str, str] | None = None, can_draft: bool = True) -> str:
    if not rows:
        return "\n\n".join(p for p in (_nothing(meta), *_footer(rows, meta)) if p)
    shown = rows[:MAX_ROWS_SHOWN]
    lines = [header_line(rows, groups), "", "| # | Resource | Kind | Idle | Monthly | Owner |", "|---|---|---|---|---|---|"]
    lines += [f"| {r.id} | `{_cell(r.resource_id)}` | {_cell(kind_cell(r))} | {idle_cell(r)} | {cost_cell(r)} | "
              f"{_cell(owner_cell(r))} |" for r in shown]
    if len(rows) > len(shown):
        lines.append(f"\nAnd {len(rows) - len(shown)} more not shown here (all are in the data part).")
    currency, total = main_total(rows)
    blocks = ["\n".join(lines), f"About {pricing.money(total * 12)} {currency}/year at this rate.",
              _owners_line(groups, outcomes or {}), *_footer(rows, meta)]
    if can_draft:
        blocks.append(_next_step(groups))
    return "\n\n".join(b for b in blocks if b)


def _nothing(meta: ListMeta) -> str:
    if not meta.resources:
        return "I couldn't read any resource from that paste."
    return f"Nothing in this inventory is idle by my rules ({meta.resources} resources checked)."


def _owners_line(groups: Sequence[Group], outcomes: Mapping[str, str]) -> str:
    parts = []
    for g in groups:
        done = f", {outcomes[g.key]}" if g.key in outcomes else ""
        parts.append(f"{g.label}: {len(g.rows)} resource{'s' if len(g.rows) != 1 else ''}, "
                     f"{pricing.money(g.total)}/month, via {g.delivery}{done}")
    return "Owners by monthly cost: " + "; ".join(parts) + "." if parts else ""


def _footer(rows: Sequence[Row], meta: ListMeta) -> list[str]:
    lines: list[str] = []
    if meta.kept_by_tag:
        lines.append(f"Kept by tag: {meta.kept_by_tag}.")
    if meta.not_evaluated:
        parts = [f"{n} {rules.LABELS.get(rule, rule)} ({_FIELD_WORDS.get(f, f'missing {f}')})"
                 for rule, missing in meta.not_evaluated.items() for f, n in missing.items()]
        lines.append("Not evaluated (never flagged): " + "; ".join(parts) + ".")
    unknown = [r for r in rows if r.cost is None]
    if unknown:
        ids = ", ".join(f"#{r.id} {r.resource_id} ({r.detail})" for r in unknown[:10])
        lines.append(f"Cost unknown, not in totals: {ids}.")
    if meta.below_min_cost:
        lines.append(f"Below min cost {meta.min_cost}: {meta.below_min_cost} left out.")
    if meta.owner_filter:
        lines.append(f"Only resources owned by \"{meta.owner_filter}\" are shown.")
    if meta.unreadable:
        lines.append(f"Unreadable rows: {meta.unreadable} of {meta.total_rows} (no id, wrong cell count or unknown "
                     "type).")
    if meta.over_limit:
        lines.append(f"Only the first {meta.resources} resources were read; {meta.over_limit} more were not.")
    if any(r.estimated for r in rows):
        lines.append(f"Estimates use us-east-1 on-demand list prices as of {pricing.PRICES_AS_OF}; paste a "
                     "monthly_cost column for actual costs.")
    lines += list(meta.notes)
    return lines


def _next_step(groups: Sequence[Group]) -> str:
    known = [g for g in groups if g.known]
    if not groups:
        return ""
    return (f"Drafts ready for {len(known)} owner{'s' if len(known) != 1 else ''}. Say \"draft for #1\" or \"draft "
            "for all known owners\". I never delete, stop or change anything.")


def render_for_model(rows: Sequence[Row], meta: ListMeta, outcomes: Mapping[str, str]) -> str:
    """The stored list as plain lines for the follow-up model (inside a fence)."""
    lines = [header_line(rows, _groups_of(rows)) if rows else "No idle resources were found."]
    for r in rows:
        evidence = f"; evidence: {r.evidence_quote} ({r.evidence_link})" if r.evidence_link else ""
        state = f"; message {outcomes[r.owner_key]}" if r.owner_key in outcomes else ""
        lines.append(f"#{r.id} {r.resource_id}{f' ({r.name})' if r.name else ''}: {kind_cell(r)}, {r.rule}, idle "
                     f"{idle_cell(r)}, monthly {cost_cell(r)} [{r.cost_basis}], confidence {r.confidence}, owner "
                     f"{owner_cell(r)}, delivery {r.delivery}{evidence}{state}")
    lines += _footer(rows, meta)
    return "\n".join(lines)


def _groups_of(rows: Sequence[Row]) -> list[Group]:
    return list(regroup(rows))


def envelope(rows: Sequence[Row], groups: Sequence[Group], meta: ListMeta) -> dict:
    currency, total = main_total(rows)
    idle = [_item(r) for r in rows]
    return {
        "summary": header_line(rows, groups) if rows else _nothing(meta),
        "items": idle,
        "confidence": confidence(rows, meta),
        "needs_human": needs_human(rows),
        "idle": idle,
        "currency": currency,
        "total_monthly": str(pricing.cents(total)),
        "annualised": str(pricing.cents(total * 12)),
        "totals_by_currency": {c: str(pricing.cents(v)) for c, v in totals(rows).items()},
        "unknown_cost": [r.resource_id for r in rows if r.cost is None],
        "not_evaluated": {k: dict(v) for k, v in meta.not_evaluated.items()},
        "kept_by_tag": meta.kept_by_tag,
        "below_min_cost": meta.below_min_cost,
        "owners": [{"owner": g.label, "resources": len(g.rows), "monthly_cost": str(pricing.cents(g.total)),
                    "delivery": g.delivery, "draft_ref": f"owner:{g.key}"} for g in groups],
        "prices_as_of": pricing.PRICES_AS_OF,
        "notes": list(meta.notes),
    }


def _item(r: Row) -> dict:
    return {"number": int(r.id or 0), "resource": r.resource_id, "name": r.name or None, "kind": r.kind,
            "detail": r.detail, "rule": r.rule, "idle_days": r.idle_days, "idle_basis": r.idle_basis,
            "confidence": r.confidence, "monthly_cost": r.monthly_cost or None, "cost_basis": r.cost_basis,
            "currency": r.currency, "owner": r.owner or None, "owner_source": r.owner_source,
            "delivery": r.delivery, "draft": f"owner:{r.owner_key}",
            "evidence": {"link": r.evidence_link, "quote": r.evidence_quote} if r.evidence_link else None}


def render_copyable(ref: str, who: str, subject: str, body: str, reason: str) -> str:
    """A message the agent cannot send, offered for the user to copy."""
    subject_line = f"Subject: {subject}\n" if subject else ""
    return f"{ref} ({who}): {reason} Here is the text to copy:\n\n{subject_line}{body}"


def never_deletes_reply(groups: Sequence[Group]) -> str:
    """"Delete them all": the fixed refusal plus the list to action, per owner."""
    if not groups:
        return ""
    lines = ["Here is the list to action, by owner (ask each owner, or delete them yourself after checking):"]
    for g in groups:
        ids = ", ".join(r.resource_id for r in g.rows)
        lines.append(f"- {g.label} ({pricing.money(g.total)}/month): {ids}")
    return "\n".join(lines)
