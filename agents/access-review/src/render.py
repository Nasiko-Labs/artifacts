"""Pure: the markdown reply and the envelope, both from the stored review (findings only, never the raw
paste). Rendering is code: no raw HTML, no images, and no link that did not come from the input."""

from __future__ import annotations

from collections.abc import Sequence

from drafts import Removal, by_system
from findings import Finding
from policy import Policy

MAX_FINDING_LINES = 60
PRIVILEGED = "privileged"   # a filter: every privileged finding


def clean(text: str) -> str:
    """One line, no HTML angle brackets, no markdown emphasis or code markers from the data."""
    return " ".join(str(text).replace("<", "‹").replace(">", "›").replace("`", "'").replace("*", "").split())


def findings_of(review: dict) -> tuple[Finding, ...]:
    return tuple(Finding.from_dict(f) for f in review.get("findings") or ())


def removals_of(review: dict) -> tuple[Removal, ...]:
    return tuple(Removal.from_dict(r) for r in review.get("removals") or ())


def select(items: Sequence[Finding], kind: str = "", system: str = "") -> tuple[Finding, ...]:
    out = tuple(items)
    if kind == PRIVILEGED:
        out = tuple(f for f in out if f.privileged)
    elif kind:
        out = tuple(f for f in out if f.kind == kind)
    if system:
        out = tuple(f for f in out if f.system.casefold() == system.casefold())
    return out


def select_removals(items: Sequence[Removal], kind: str = "", system: str = "") -> tuple[Removal, ...]:
    out = tuple(r for r in items if not kind or kind == PRIVILEGED or r.rule.endswith(f" {kind}"))
    return tuple(r for r in out if not system or r.system.casefold() == system.casefold())


def header(review: dict) -> str:
    parts = [f"**Access review** · {review.get('holders', 0)} holders reviewed"]
    if review.get("export_rows"):
        parts.append(f"export {review['export_rows']} rows")
    parts.append(f"roster {review['roster_rows']}" if review.get("roster_rows") else "no roster")
    org = review.get("org") or {}
    if org:
        parts.append(f"code-host org {clean(org['name'])} ({org['members']} members, {org['outside']} outside "
                     f"collaborators, {org['repos_checked']} repos checked)")
    lines = [" · ".join(parts), "Policy: " + Policy.from_dict(review.get("policy")).describe()]
    if review.get("changed"):
        lines.append("Policy changed this turn: " + "; ".join(review["changed"]))
    return "\n".join(lines)


def finding_line(f: Finding) -> str:
    who = clean(f.person) + (f" ({clean(f.email)})" if f.email and f.email != f.person else "")
    weak = " · weak match (name only), not drafted" if f.weak_match else ""
    return (f"- **{f.severity.upper()}** {f.kind}: {who} · {clean(f.role)} on {clean(f.system)} · "
            f"{clean(f.evidence)}{weak} · rule {f.rule.split(' ', 1)[0]}")


def findings_block(items: Sequence[Finding], label: str = "Findings") -> str:
    if not items:
        return f"**{label}:** none."
    lines = [f"**{label} ({len(items)})**", *(finding_line(f) for f in items[:MAX_FINDING_LINES])]
    if len(items) > MAX_FINDING_LINES:
        lines.append(f"- … {len(items) - MAX_FINDING_LINES} more in the data part of this reply.")
    return "\n".join(lines)


def removals_block(items: Sequence[Removal]) -> str:
    if not items:
        return "Removal requests drafted: 0. Nothing was removed or sent."
    lines = [f"**Removal requests drafted: {len(items)}** (copy below). Nothing was removed or sent."]
    for system, group in by_system(items).items():
        lines.append(f"\n_{clean(system)}_")
        lines.extend(f"> {clean(r.request_text)}" for r in group)
    return "\n".join(lines)


def coverage_block(review: dict) -> str:
    notes = list(review.get("coverage") or ())
    if review.get("on_leave"):
        notes.append(f"{review['on_leave']} holder(s) are on leave per the roster; treated as active.")
    body = "\n".join(f"- {clean(n)}" for n in notes)
    tail = f"Confidence: {review.get('confidence', 'low')}."
    return f"**Coverage**\n{body}\n\n{tail}" if notes else tail


def report(review: dict, kind: str = "", system: str = "") -> str:
    items = select(findings_of(review), kind, system)
    removals = select_removals(removals_of(review), kind, system)
    label = "Findings" + (f" · {kind}" if kind else "") + (f" · only {clean(system)}" if system else "")
    return "\n\n".join(x for x in (header(review), clean(review.get("headline") or ""), findings_block(items, label),
                                   removals_block(removals), coverage_block(review)) if x)


def drafts_only(review: dict, kind: str = "", system: str = "", lead: str = "") -> str:
    removals = select_removals(removals_of(review), kind, system)
    body = removals_block(removals) if removals else (
        "No removal requests match" + (f" {kind}" if kind else "") + (f" on {clean(system)}" if system else "")
        + ". Requests are drafted only for removal-candidate findings with a strong identity match.")
    return "\n\n".join(x for x in (lead, body) if x)


def envelope(review: dict, kind: str = "", system: str = "") -> dict:
    items = [f.public() for f in select(findings_of(review), kind, system)]
    return {"summary": review.get("headline") or "", "items": items, "confidence": review.get("confidence", "low"),
            "needs_human": bool(review.get("needs_human")), "review": items,
            "removals": [r.public() for r in select_removals(removals_of(review), kind, system)],
            "service_accounts": [i for i in items if i["kind"] == "service_account_review"],
            "policy": Policy.from_dict(review.get("policy")).to_dict(), "coverage": list(review.get("coverage") or ())}
