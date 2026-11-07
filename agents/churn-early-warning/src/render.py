"""User-facing text and the envelope: the ranked table, the win-back group, the footer, the list given to
the follow-up and draft models, and an outreach email to copy. Every number here is computed in code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

import mrr
import outreach
import scoring
import signals
from scoring import Row

_OUTCOME_LABELS = {"sent": "outreach sent", "skipped": "draft skipped", "denied": "send denied",
                   "blocked": "sending blocked", "not_sent": "not sent"}


@dataclass(frozen=True)
class ListMeta:
    """What the headline and footer say, kept so a follow-up can re-render without a scan."""

    lookahead: int
    lookback: int
    mode: str = "live"                  # "live" | "pasted"
    healthy: int = 0
    below_min: int = 0
    left_out: int = 0
    dropped: int = 0
    skipped_rows: int = 0
    min_mrr: str = ""
    notes: tuple[str, ...] = ()
    unchecked: tuple[str, ...] = ()     # whole sources not checked this scan: "email (not connected)"
    confidence: str = "high"
    needs_human: bool = False

    def to_dict(self) -> dict:
        return {**asdict(self), "notes": list(self.notes), "unchecked": list(self.unchecked)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> ListMeta:
        raw = dict(data or {})
        names = {f.name for f in fields(ListMeta)}
        values = {k: v for k, v in raw.items() if k in names}
        return ListMeta(**{**values, "notes": tuple(values.get("notes") or ()),
                           "unchecked": tuple(values.get("unchecked") or ())})


def headline(rows: Sequence[Row], meta: ListMeta) -> str:
    at_risk = [r for r in rows if r.group == "at_risk"]
    high = [r for r in at_risk if r.band == "High"]
    source = " from pasted rows" if meta.mode == "pasted" else ""
    text = (f"Churn risk{source}, renewals in the next {meta.lookahead} days and changes in the last "
            f"{meta.lookback} days: {len(at_risk)} at risk, {len(high)} high")
    totals = scoring.mrr_at_risk(at_risk)
    if totals:
        text += " (MRR at risk " + ", ".join(mrr.money(v, c) for c, v in sorted(totals.items())) + ")"
    return text


def render_list(rows: Sequence[Row], meta: ListMeta, outcomes: Mapping[str, str]) -> str:
    at_risk = [r for r in rows if r.group == "at_risk"]
    lost = [r for r in rows if r.group == "cancelled"]
    parts = [headline(rows, meta) + "."]
    parts.append(_table(at_risk, meta, outcomes) if at_risk else "No account at risk to list.")
    if lost:
        parts.append("**Already cancelled (win-back)**\n\n" + _table(lost, meta, outcomes))
    parts.append(_footer(rows, meta))
    parts.extend(meta.notes)
    return "\n\n".join(p for p in parts if p)


def _table(rows: Sequence[Row], meta: ListMeta, outcomes: Mapping[str, str]) -> str:
    header = "| # | Account | MRR | Risk | Signals | Suggested outreach |\n|---|---|---|---|---|---|"
    return "\n".join([header, *(_row(r, meta, outcomes) for r in rows)])


def _row(r: Row, meta: ListMeta, outcomes: Mapping[str, str]) -> str:
    a = r.account
    name = f"[{_cell(a.name)}]({a.link})" if a.link.startswith(("http://", "https://")) else _cell(a.name)
    done = outcomes.get(r.key)
    advice = f"{outreach.LABELS.get(r.outreach, r.outreach)}: {r.angle}" + (f" ({_OUTCOME_LABELS.get(done, done)})"
                                                                             if done else "")
    cells = (r.id, name, mrr.money(a.mrr_minor, a.currency), f"{r.band} ({r.risk_score:.1f})",
             signal_text(r, meta), advice)
    return "| " + " | ".join(_cell(str(c)) if i != 1 else c for i, c in enumerate(cells)) + " |"


def signal_text(r: Row, meta: ListMeta) -> str:
    shown = [_signal(s) for s in r.account.signals]
    if r.account.reason:
        shown.append(f'reason given: "{r.account.reason}"')
    own = [s for s in r.account.not_checked if not any(u.startswith(s) for u in meta.unchecked)]
    if own:
        shown.append("not checked: " + ", ".join(own))
    return "; ".join(shown)


def _signal(s: signals.Signal) -> str:
    label = signals.LABELS.get(s.kind, s.kind)
    return f'{label}: "{s.evidence}"' if s.kind == "complaint" else f"{label} ({s.evidence})"


def _footer(rows: Sequence[Row], meta: ListMeta) -> str:
    notes = [f"Healthy renewals: {meta.healthy}."]
    if meta.unchecked:
        notes.append("Not checked: " + ", ".join(meta.unchecked) + ".")
    if meta.below_min:
        notes.append(f"{meta.below_min} account(s) below the {meta.min_mrr} MRR minimum not shown.")
    if meta.left_out:
        notes.append(f"Showing the top {scoring.MAX_LISTED}; {meta.left_out} more not shown.")
    if meta.dropped:
        notes.append(f"Left out {meta.dropped} record(s) I couldn't verify in the tool output.")
    if meta.skipped_rows:
        notes.append(f"Skipped {meta.skipped_rows} pasted row(s) without an account name or MRR.")
    if rows:
        notes.append("Risk is a ranking from these signals, not a prediction. Say \"draft outreach for #1\" and "
                     "I'll draft an email for you to approve before anything is sent.")
    return " ".join(notes)


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def envelope(rows: Sequence[Row], meta: ListMeta, drafts: Mapping[str, Mapping[str, str]] | None = None) -> dict:
    drafted = dict(drafts or {})
    at_risk = [_item(r, meta, drafted.get(r.key)) for r in rows if r.group == "at_risk"]
    lost = [_item(r, meta, drafted.get(r.key)) for r in rows if r.group == "cancelled"]
    return {"summary": headline(rows, meta), "items": at_risk, "confidence": meta.confidence,
            "needs_human": meta.needs_human, "at_risk": at_risk, "healthy_renewals": meta.healthy,
            "already_cancelled": lost, "not_checked": list(meta.unchecked), "notes": list(meta.notes)}


def _item(r: Row, meta: ListMeta, draft: Mapping[str, str] | None) -> dict:
    a = r.account
    return {"id": r.id, "account": a.name, "account_id": a.id, "email": a.email or None,
            "mrr": float(mrr.to_major(a.mrr_minor, a.currency)), "currency": a.currency or None,
            "risk_score": r.risk_score, "band": r.band,
            "signals": [{"kind": s.kind, "weight": s.weight, "evidence": s.evidence, "link": s.link or None}
                        for s in a.signals],
            "not_checked": list(meta.unchecked) + [s for s in a.not_checked
                                                   if not any(u.startswith(s) for u in meta.unchecked)],
            "suggested_outreach": {"type": r.outreach, "angle": r.angle}, "draft": dict(draft) if draft else None,
            "link": a.link or None}


def render_for_model(rows: Sequence[Row], meta: ListMeta, outcomes: Mapping[str, str]) -> str:
    """The stored list as the follow-up and draft models see it (fenced by the caller)."""
    lines = [headline(rows, meta)]
    for r in rows:
        a = r.account
        done = outcomes.get(r.key)
        status = f"; {_OUTCOME_LABELS.get(done, done)}" if done else ""
        lines.append(f"#{r.id} [{r.group}; {r.band} risk {r.risk_score:.1f}{status}] {a.name} <{a.email or 'no email'}>"
                     f", MRR {mrr.money(a.mrr_minor, a.currency)}; signals: {signal_text(r, meta)}; suggested "
                     f"outreach: {r.outreach} ({r.angle}); latest email subject: {a.complaint_subject or 'none'}")
    lines.append(f"Healthy renewals: {meta.healthy}. Not checked: {', '.join(meta.unchecked) or 'nothing'}.")
    return "\n".join(lines)


def render_copyable(row_id: str, who: str, subject: str, body: str, reason: str) -> str:
    """An outreach email the agent cannot send, offered for the user to copy."""
    subject_line = f"Subject: {subject}\n" if subject else ""
    return f"#{row_id} to {who}: {reason} Here is the text to copy:\n\n{subject_line}{body}"
