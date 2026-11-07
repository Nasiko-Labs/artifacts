"""User-facing text: the audit table, the listing the follow-up model sees, and text to copy."""

from __future__ import annotations

from collections.abc import Sequence

from derive import LABELS, Row
from quarter import Quarter

_DISAGREEMENT = {"reported_better": "reported better than evidence", "reported_worse": "reported worse than evidence",
                 "unreported_risk": "not reported, evidence shows risk"}
SAVE_HINT = "Say \"write it as a document\" or \"post it to #channel\" to save it."


def label(status: str) -> str:
    return LABELS.get(status, status)


def disagreement_text(row: Row) -> str:
    return _DISAGREEMENT.get(row.disagreement or "", "")


def render_audit(rows: Sequence[Row], quarter: Quarter, not_audited: Sequence[str], notes: Sequence[str] = (),
                 dropped: int = 0) -> str:
    flagged = sum(1 for r in rows if r.disagreement)
    heading = (f"Commitment audit for {quarter.label} ({quarter.start.isoformat()} to {quarter.end.isoformat()}): "
               f"{len(rows)} audited, {flagged} where the reported status disagrees with the evidence.")
    parts = [heading, _table(rows)]
    if not_audited:
        parts.append("**Not audited this run** (over the limit): " + ", ".join(_cell(n) for n in not_audited))
    if dropped:
        parts.append(f"Left out {dropped} item(s) I couldn't back with tool output.")
    parts.extend(notes)
    return "\n\n".join(p for p in parts if p)


def _table(rows: Sequence[Row]) -> str:
    header = ("| # | Commitment | Owner | Reported | Evidence | Confidence | Risk | Links |\n"
              "|---|---|---|---|---|---|---|---|")
    lines = [f"| {r.number} | {_cell(r.name)} | {_cell(r.owner or '-')} | {_reported(r)} | {_evidence(r)} | "
             f"{r.confidence} | {_cell('; '.join(r.risks) or '-')} | {_cell(' '.join(r.links) or '-')} |" for r in rows]
    return "\n".join([header, *lines])


def _reported(row: Row) -> str:
    return label(row.reported)


def _evidence(row: Row) -> str:
    text = label(row.evidence)
    return f"**{text}** ({disagreement_text(row)})" if row.disagreement else text


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def render_for_followup(rows: Sequence[Row], not_audited: Sequence[str], quarter: Quarter) -> str:
    lines = [f"Quarter: {quarter.label} ({quarter.start.isoformat()} to {quarter.end.isoformat()})"]
    lines.extend(
        f"#{r.number} {r.name} | owner {r.owner or 'unknown'} | reported {label(r.reported)}"
        f"{' (' + r.reported_text + ')' if r.reported_text else ''} | evidence {label(r.evidence)} | "
        f"confidence {r.confidence} | {disagreement_text(r) or 'agrees'} | risks: {'; '.join(r.risks) or 'none'} | "
        f"links: {' '.join(r.links) or 'none'}" for r in rows)
    if not_audited:
        lines.append("Not audited this run: " + ", ".join(not_audited))
    return "\n".join(lines)


def render_copyable(reason: str, body: str) -> str:
    return f"{reason} Here is the rollup to copy:\n\n{body}"
