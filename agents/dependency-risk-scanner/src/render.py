"""Pure: the report in markdown, the facts the summary is written from, the summary check, and the
envelope. Every status, count and version comes from the scan (code); the model only writes the
two-sentence summary, which is dropped unless every number and repository in it is in the facts.
`confidence` and `needs_human` are computed here, never taken from the model."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import match
from match import Exposure
from scan import PASTED, Scan

_LABEL = {"affected": "AFFECTED", "may_resolve_affected": "may resolve affected (no lockfile)",
          "unknown": "unknown (version unreadable)", "deprecated": "DEPRECATED (declared)",
          "not_affected": "not affected"}
_ENVELOPE_FIELDS = ("repo", "manifest", "package", "ecosystem", "declared", "resolved", "scope", "status", "fixed",
                    "bump_to", "url")
_NUMBER = re.compile(r"\d+(?:\.\d+)*")
_REPO = re.compile(r"\b[\w.-]+/[\w.-]+\b")
_MAX_SUMMARY_CHARS = 280


def advisory_line(scan: Scan) -> str:
    a = scan.advisory
    if a.kind == "deprecation":
        use = f" · use {a.replacement}" if a.replacement else ""
        return f"Deprecation notice · {a.package} ({a.ecosystem or 'ecosystem unknown'}){use}"
    head = f"Advisory {a.id}" if a.id else "Advisory"
    affected = ", ".join(a.affected) if a.affected else "range not given (statuses read unknown)"
    fixed = ", ".join(a.fixed) or "none listed"
    return f"{head} · {a.package} ({a.ecosystem or 'ecosystem unknown'}) · affected {affected} · fixed {fixed}"


def exposed_repos(scan: Scan) -> list[str]:
    return list(dict.fromkeys(r.repo for r in match.ranked(scan.rows) if r.status in match.EXPOSED))


def not_affected_repos(scan: Scan) -> list[str]:
    listed = {r.repo for r in match.ranked(scan.rows)}
    return [r for r in dict.fromkeys(x.repo for x in scan.rows if x.status == "not_affected") if r not in listed]


def row_line(i: int, row: Exposure) -> str:
    where = f"{row.manifest} ({row.url})" if row.url else row.manifest
    parts = [f"`{row.package}`: `{row.declared or 'no version'}`"]
    if row.resolved:
        parts.append(f"resolved {row.resolved} (lockfile)")
    label = _LABEL.get(row.status, row.status)
    parts.append(f"{label} ({row.scope})" if row.status in ("affected", "deprecated") else label)
    if row.bump_to:
        parts.append(f"bump to {row.bump_to}" + (" (major bump)" if row.major_bump else ""))
    if row.alert:
        parts.append("the code host's own alert agrees")
    repo = "pasted" if row.repo == PASTED else row.repo
    return f"{i}. {repo}  {where}  " + " · ".join(parts)


def report(scan: Scan, summary: str, show_all: bool = False) -> str:
    rows = match.ranked(scan.rows, include_not_affected=show_all)
    scanned = len(scan.repos)
    exposed = exposed_repos(scan)
    lines = [advisory_line(scan), ""]
    if summary:
        lines += [summary, ""]
    title = "Declared (pasted manifests)" if scan.pasted else f"Exposed ({len(exposed)} of {scanned} repos scanned)"
    lines.append(title)
    lines += [row_line(i, r) for i, r in enumerate(rows, 1)] or ["None."]
    rest = _not_affected_line(scan)
    if rest:
        lines += ["", rest]
    lines += _coverage(scan)
    hint = next_step_hint(scan)
    if hint:
        lines += ["", hint]
    return "\n".join(lines)


def _not_affected_line(scan: Scan) -> str:
    if scan.pasted:
        bits = [f"{r.manifest} ({r.resolved or r.declared})" for r in scan.rows if r.status == "not_affected"]
        return "Not affected: " + ", ".join(bits) if bits else ""
    bits = []
    for repo in not_affected_repos(scan):
        versions = sorted({r.resolved or r.declared for r in scan.rows if r.repo == repo})
        bits.append(f"{repo} ({', '.join(versions)})")
    bits += [f"{repo} (not declared)" for repo in scan.not_declared if repo != PASTED]
    return "Not affected: " + ", ".join(bits) if bits else ""


def _coverage(scan: Scan) -> list[str]:
    notes = list(scan.notes)
    if scan.cut:
        notes.append(f"Not scanned (over the cap of {len(scan.repos)}): {', '.join(scan.cut)}.")
    if scan.pasted:
        notes.append("Read from pasted manifests only; nothing was fetched from a code host.")
    return (["", "Coverage:"] + [f"- {n}" for n in notes]) if notes else []


def next_step_hint(scan: Scan) -> str:
    if scan.advisory.kind == "deprecation" or scan.pasted:
        return ""
    target = next((r.repo for r in match.ranked(scan.rows) if r.bump_to and r.status in match.EXPOSED), "")
    return f'Say "open the PR for {target}" to upgrade it (each write is its own approval).' if target else ""


def counts(scan: Scan) -> dict[str, int]:
    out = {s: 0 for s in ("affected", "may_resolve_affected", "unknown", "not_affected", "deprecated")}
    for row in scan.rows:
        out[row.status] = out.get(row.status, 0) + 1
    return out


def facts(scan: Scan) -> str:
    c = counts(scan)
    lines = [advisory_line(scan), f"repositories scanned: {len(scan.repos)}",
             f"repositories exposed: {len(exposed_repos(scan))}",
             f"declarations affected: {c['affected']}",
             f"declarations that may resolve affected: {c['may_resolve_affected']}",
             f"declarations unknown: {c['unknown']}", f"declarations not affected: {c['not_affected']}",
             f"declarations of a deprecated package: {c['deprecated']}",
             f"repositories not declaring the package: {len(scan.not_declared)}"]
    lines += [row_line(i, r) for i, r in enumerate(match.ranked(scan.rows, include_not_affected=True), 1)]
    return "\n".join(lines)


def checked_summary(text: str, fact_text: str) -> bool:
    """Every number and every owner/repo in the summary appears in the facts; at most two sentences."""
    if not text or len(text) > _MAX_SUMMARY_CHARS or len(re.findall(r"[.!?](?:\s|$)", text)) > 2:
        return False
    return all(n in fact_text for n in _NUMBER.findall(text)) and all(r in fact_text for r in _REPO.findall(text))


def code_summary(scan: Scan) -> str:
    exposed = exposed_repos(scan)
    package = scan.advisory.package
    if scan.pasted:
        return f"{len(exposed)} pasted manifest set(s) declare an exposed `{package}`." if exposed else \
            f"No pasted manifest declares an exposed `{package}`."
    if not exposed:
        return f"None of the {len(scan.repos)} repositories scanned is exposed through `{package}`."
    return f"{len(exposed)} of {len(scan.repos)} repositories scanned are exposed through `{package}`."


def confidence(scan: Scan) -> str:
    if scan.pasted or scan.advisory.range_waived or not scan.advisory.affected and scan.advisory.kind != "deprecation":
        return "low"
    statuses = {r.status for r in scan.rows}
    if scan.notes or scan.cut or statuses & {"may_resolve_affected", "unknown"}:
        return "medium"
    return "high"


def needs_human(scan: Scan) -> bool:
    return any(r.status == "affected" and r.scope == "runtime" or r.major_bump for r in scan.rows)


def item(row: Exposure) -> dict:
    data = row.to_dict()
    return {k: data.get(k) for k in _ENVELOPE_FIELDS}


def envelope(scan: Scan, summary: str, show_all: bool = False, prs: Sequence[dict] = ()) -> dict:
    items = [item(r) for r in match.ranked(scan.rows, include_not_affected=show_all)]
    return {"summary": summary or code_summary(scan), "items": items, "confidence": confidence(scan),
            "needs_human": needs_human(scan), "exposed_repos": items, "prs": list(prs),
            "advisory": scan.advisory.to_dict(), "coverage": list(scan.notes)}


def repos_with_upgrade(rows: Iterable[Exposure]) -> list[str]:
    """Repositories that have at least one declaration a bump can fix, in rank order."""
    return list(dict.fromkeys(r.repo for r in match.ranked(rows)
                              if r.bump_to and r.status in ("affected", "may_resolve_affected")))
