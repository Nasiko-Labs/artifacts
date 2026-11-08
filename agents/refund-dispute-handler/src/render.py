"""Pure rendering, all in code: the case reply (markdown), the open-dispute list, the envelope (and its JSON
block for workflows), the compact case the model reads on follow-ups and drafts, and the copyable draft when
nothing can be sent. URLs come only from checked tool output."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from case import Dispute, Policy, money
from evidence import Deadline, EvidenceItem, deadline, reason_of
from facts import Facts
from policy import Recommendation

_LABEL = {"approve": "APPROVE", "partial": "PARTIAL refund", "deny": "DENY", "needs_human": "NEEDS A HUMAN",
          "contest": "CONTEST the dispute", "accept_dispute": "ACCEPT the dispute"}


@dataclass(frozen=True)
class CaseView:
    """Everything the reply and the envelope are built from (all checked and computed already)."""

    case_type: str                     # refund_request | dispute
    facts: Facts
    recommendation: Recommendation
    evidence: tuple[EvidenceItem, ...]
    policy: Policy
    customer_email: str | None = None
    customer_name: str | None = None
    charge_link: str | None = None
    dispute: Dispute | None = None
    deadline: Deadline | None = None
    packet: str | None = None
    notes: tuple[str, ...] = ()
    test_mode: bool = False
    confidence: str = "medium"
    needs_human: bool = False
    reply_draft: Mapping | None = None
    extra: Mapping = field(default_factory=dict)


def amount_text(rec: Recommendation, currency: str) -> str:
    if rec.decision == "approve":
        return f" full refund {money(rec.amount_minor, currency)}"
    if rec.decision == "partial":
        return f" {money(rec.amount_minor, currency)}"
    return ""


def summary(view: CaseView) -> str:
    rec, f = view.recommendation, view.facts
    if view.case_type == "dispute" and view.dispute is not None:
        d = view.dispute
        return (f"Dispute {d.id} ({reason_of(d)}, {money(d.amount_minor, d.currency)}): "
                f"{_LABEL.get(rec.decision, rec.decision)}")
    who = view.customer_email or "unknown customer"
    return f"Refund request from {who} for {f.charge_id or 'an unverified charge'}: " \
           f"{_LABEL.get(rec.decision, rec.decision)}{amount_text(rec, f.currency)}"


def render_case(view: CaseView) -> str:
    return render_dispute(view) if view.case_type == "dispute" else render_refund(view)


def render_refund(view: CaseView) -> str:
    f, rec = view.facts, view.recommendation
    mode = " (test mode)" if view.test_mode else ""
    who = f"{view.customer_name} <{view.customer_email}>" if view.customer_name else (view.customer_email or "unknown")
    charge = (f"charge {f.charge_id} {money(f.amount_minor, f.currency)} on {f.charge_date}" if f.charge_id
              else "charge not verified")
    lines = [f"**Refund request:** {who}, {charge}{mode}",
             (f"**Recommendation: {_LABEL.get(rec.decision, rec.decision)}{amount_text(rec, f.currency)}** "
             f"(policy: {_policy_line(view.policy)})"), "", "Why:"]
    lines += [f"- {r}" for r in rec.reasons]
    lines.append(f"- Prior refunds in 12 months: {f.prior_refund_count_12m}; prior disputes: {f.prior_disputes}")
    if rec.flags:
        lines.append(f"Flags: {'; '.join(rec.flags)}")
    lines += _common(view)
    if view.reply_draft:
        lines += ["", "Reply draft ready (pending your decision). Say \"draft the reply\" any time to review it."]
    return "\n".join(lines)


def render_dispute(view: CaseView) -> str:
    d, rec, when = view.dispute, view.recommendation, view.deadline
    mode = " (test mode)" if view.test_mode else ""
    lines = [(f"**Dispute {d.id}** on charge {d.charge_id or 'unknown'}: {money(d.amount_minor, d.currency)}, "
             f"reason {reason_of(d)}, status {d.status or 'unknown'}{mode}")]
    if d.network_reason_code:
        lines.append(f"Network reason code: {d.network_reason_code}")
    if when and when.closed:
        lines.append(f"**Evidence window closed** on {when.due}.")
    elif when and when.urgent:
        lines.append(f"**URGENT: evidence due in {when.days_left} days ({when.due}).**")
    elif when and when.due:
        lines.append(f"Evidence due by {when.due} ({when.days_left} days left).")
    lines += ["", f"**Recommendation: {_LABEL.get(rec.decision, rec.decision)}**"]
    lines += [f"- {r}" for r in rec.reasons]
    lines += ["", "Evidence checklist:", "| Item | Available | Detail | Source |", "|---|---|---|---|"]
    lines += [f"| {_cell(i.name)} | {'yes' if i.available else 'no'} | {_cell(i.value)} | {_cell(i.source_id or '-')} |"
              for i in view.evidence]
    if view.packet:
        lines += ["", "Evidence packet (paste it into your payments dashboard; add the items marked \"add yours\"):",
                  "```text", view.packet, "```"]
    lines += _common(view, show_evidence=False)
    return "\n".join(lines)


def _common(view: CaseView, show_evidence: bool = True) -> list[str]:
    lines: list[str] = []
    if show_evidence and view.evidence:
        lines += ["", "Evidence:"]
        lines += [f"- {i.name}: {i.value} ({i.source_id})" + (f" {i.link}" if (i.link or "").startswith("http") else "")
                  for i in view.evidence]
    lines += [""] + [f"_{n}_" for n in view.notes] if view.notes else []
    human = "yes" if view.needs_human else "no"
    lines += ["", (f"Confidence: {view.confidence}. Needs a human: {human}. I recommend and draft; you issue refunds and "
              "submit evidence in your payments dashboard.")]
    return lines


def _policy_line(policy: Policy) -> str:
    return (f"{policy.source}: {policy.window_days}-day window, auto-approve up to "
            f"{money(policy.auto_approve_max_minor, 'USD').split(' ')[0]}, annual plans {policy.annual_refund}")


def _cell(text: str) -> str:
    return (text or "").replace("|", "/").replace("\n", " ")


def render_dispute_list(items: Sequence[Dispute], now: datetime, notes: Sequence[str] = ()) -> str:
    lines = [f"Open disputes ({len(items)}):", "| # | Dispute | Charge | Amount | Reason | Status | Evidence due |",
             "|---|---|---|---|---|---|---|"]
    for n, d in enumerate(items, start=1):
        when = deadline(d, now)
        due = "unknown" if not when.due else (f"{when.due} (closed)" if when.closed else
                                              f"{when.due} ({when.days_left} days{', URGENT' if when.urgent else ''})")
        lines.append(f"| {n} | {d.id} | {d.charge_id or '-'} | {money(d.amount_minor, d.currency)} | {reason_of(d)} | "
                     f"{d.status or '-'} | {due} |")
    lines += ["", "Ask \"what do I do about dp_...\" for the recommendation and evidence packet of one of them."]
    lines += [f"_{n}_" for n in notes]
    return "\n".join(lines)


def envelope(view: CaseView) -> dict:
    d = view.dispute
    return {
        "summary": summary(view), "items": [i.to_dict() for i in view.evidence], "confidence": view.confidence,
        "needs_human": view.needs_human, "recommendation": view.recommendation.to_dict(),
        "reply_draft": dict(view.reply_draft) if view.reply_draft else None,
        "evidence": [i.to_dict() for i in view.evidence], "case_type": view.case_type,
        "dispute": None if d is None else {
            "id": d.id, "charge_id": d.charge_id, "amount_minor": d.amount_minor, "currency": d.currency,
            "reason": reason_of(d), "status": d.status, "evidence_due_by": view.deadline.due if view.deadline else None,
            "days_to_evidence_due": view.deadline.days_left if view.deadline else None,
            "urgent": bool(view.deadline and view.deadline.urgent), "packet": view.packet},
        "facts": view.facts.to_dict(), "policy": view.policy.to_dict(), "notes": list(view.notes),
    }


def json_block(env: Mapping) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=1) + "\n```"


def model_case(env: Mapping) -> str:
    """The compact checked case the model reads on follow-ups and drafts (no links)."""
    keep = {k: env.get(k) for k in ("summary", "case_type", "recommendation", "facts", "dispute", "notes")}
    keep["evidence"] = [{k: i.get(k) for k in ("name", "available", "value", "source_id")}
                        for i in env.get("evidence") or ()]
    return json.dumps(keep, ensure_ascii=False, indent=1)


def render_copyable(label: str, who: str, subject: str, body: str, reason: str) -> str:
    head = f"{label}: {reason} Here is the text to send yourself to {who}:"
    subject_line = f"Subject: {subject}\n" if subject else ""
    return f"{head}\n\n```text\n{subject_line}{body}\n```"
