"""Pure: the two comparison forms, from parsed inputs and fetched files to ranked drift records.

Form A: a refresh-only report; each changed attribute is a drift, located in the IaC file when one was
fetched (the IaC value replaces the last known state as `expected`, flagged when they differ).
Form B: an IaC file against a pasted live object, compared key by key in code.
Both: the last code author is the newest commit that touched the file; the manual actor comes only
out of pasted audit events (audit.py).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import audit
import drift as dr
import iac
import parse_drift
from compare import Diff, compare
from hcl_lite import has_unresolved
from records import Commit
from severity import redact

MAX_RESOURCES = 15


@dataclass(frozen=True)
class Analysis:
    form: str
    drifts: tuple[dr.Drift, ...]
    extras: tuple[dr.Drift, ...] = ()
    compared: int = 0
    unresolved: int = 0
    resources: int = 0
    iac_source: str = "none"          # code host | pasted | none
    unlocated: tuple[str, ...] = ()   # resources whose IaC block was not found
    capped: tuple[str, ...] = ()      # resources over the fetch cap
    audit_given: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Sources:
    """What the code host (or the paste) gave: file texts by path and the commits per path."""

    files: Mapping[str, str]
    commits: Mapping[str, tuple[Commit, ...]]
    origin: str = "none"


def form_a(report: parse_drift.Report, sources: Sources, events: Sequence[audit.Event], now: datetime,
           wanted: Sequence[str] = ()) -> Analysis:
    blocks = _blocks(sources.files)
    drifts, compared, unresolved, unlocated = [], 0, 0, []
    for change in report.changes:
        found, n, skipped = _change_drifts(change)
        compared, unresolved = compared + n, unresolved + skipped
        where = blocks.get(change.address)
        if where is None and sources.origin != "none":
            unlocated.append(change.address)
        located = [_locate(d, change, where, sources) for d in found]
        who = audit.changed_by(events, change.identifiers(), now)
        drifts += [replace(d, changed_by=who) for d in located]
    capped = tuple(a for a in wanted[MAX_RESOURCES:])
    return Analysis("A", dr.ranked(drifts), (), compared, unresolved, len(report.changes), sources.origin,
                    tuple(unlocated), capped, bool(events))


def _change_drifts(change: parse_drift.ResourceChange) -> tuple[list[dr.Drift], int, int]:
    ids, hints = change.identifiers(), (change.address,)
    if change.deleted:
        gone = Diff((), "missing_live", "present", "deleted")
        return [dr.from_diff(change.address, gone, ids, hints)], 1, 0
    result = compare(change.expected, change.actual)
    found = [dr.from_diff(change.address, d, ids, hints) for d in (*result.diffs, *result.extras)]
    hidden = [dr.from_diff(change.address, Diff((name,), "changed", "[redacted]", "[redacted]"), ids, hints,
                           ("sensitive",)) for name in change.sensitive]
    return found + hidden, result.compared + len(hidden), max(0, result.unresolved - len(hidden))


def _locate(d: dr.Drift, change: parse_drift.ResourceChange, where, sources: Sources) -> dr.Drift:
    if where is None:
        return d
    path, doc = where
    located = dr.with_location(d, path, doc.line_of(d.path) if d.path else doc.line)
    located = _authored(located, sources.commits.get(path, ()))
    if not d.path or "sensitive" in d.flags:
        return located
    found, value = parse_drift.value_at(doc.body, d.path)
    if not found:
        return located
    if has_unresolved(value):
        return replace(located, flags=(*located.flags, "unresolved"))
    _, state_value = parse_drift.value_at(change.expected or {}, d.path)
    differs = compare(value, state_value).diffs
    flags = (*located.flags, "iac_differs_from_state") if differs else located.flags
    return replace(located, expected=redact(d.path, iac.plain(value)), flags=flags)


def _authored(d: dr.Drift, commits: Sequence[Commit]) -> dr.Drift:
    latest = max(commits, key=lambda c: c.date, default=None)
    return replace(d, last_code_author=latest.author, last_code_change=latest.date) if latest else d


def _blocks(files: Mapping[str, str]) -> dict:
    out: dict = {}
    for path, text in files.items():
        if path.endswith((".yaml", ".yml", ".json")):
            continue
        try:
            docs = iac.parse_docs(text, path)
        except iac.Unreadable:
            continue
        for doc in docs:
            out.setdefault(doc.name, (path, doc))
    return out


def form_b(iac_path: str, iac_text: str, live_text: str, sources: Sources, events: Sequence[audit.Event],
           now: datetime) -> Analysis | str:
    """The analysis, or a reply explaining why the two could not be compared."""
    try:
        iac_docs = iac.parse_docs(iac_text, iac_path)
    except iac.Unreadable:
        return f"I couldn't read {iac_path} as Terraform, YAML or JSON, so nothing was compared."
    try:
        live_docs = iac.parse_docs(live_text)
    except iac.Unreadable:
        return "I couldn't read the pasted live object as YAML, JSON or `terraform state show` output."
    pairs = iac.pair(iac_docs, live_docs)
    if not pairs:
        names = ", ".join(d.name for d in iac_docs[:8])
        live = ", ".join(d.name for d in live_docs[:8])
        return f"Nothing in {iac_path} ({names}) matches the pasted live object ({live}), so nothing was compared."
    drifts, extras, compared, unresolved = [], [], 0, 0
    commits = sources.commits.get(iac_path, ())
    for code, live in pairs:
        result = compare(code.body, live.body)
        ids = _live_ids(live)
        hints = (iac_path, live.namespace, code.name)
        made = [dr.with_location(dr.from_diff(code.name, d, ids, hints), iac_path, code.line_of(d.path))
                for d in result.diffs]
        extra = [replace(dr.from_diff(code.name, d, ids, hints), severity="low") for d in result.extras]
        who = audit.changed_by(events, ids, now)
        drifts += [replace(_authored(d, commits), changed_by=who) for d in made]
        extras += [replace(d, changed_by=who) for d in extra]
        compared, unresolved = compared + result.compared, unresolved + result.unresolved
    return Analysis("B", dr.ranked(drifts), tuple(extras), compared, unresolved, len(pairs), sources.origin,
                    audit_given=bool(events))


def _live_ids(doc: iac.Doc) -> tuple[str, ...]:
    body = doc.body if isinstance(doc.body, dict) else {}
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    found = [doc.name, meta.get("name"), *(body.get(k) for k in ("id", "arn", "name", "GroupId", "DBInstanceIdentifier"))]
    return tuple(dict.fromkeys(str(v) for v in found if isinstance(v, str) and len(v) >= 3))
