"""Pure: turn the checked fanout records into the card's facts (JSON-safe), with every number computed by
scoring.py. Changes after the start are kept for the timeline only, never as suspects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import coverage as cov
import records as r
import scoring
from investigation import Investigation
from windows import iso, parse_time

NO_SUSPECT = "No change in the last 24 h lines up with the start."
NO_DEPLOYS = "No deploy records were found, so only merged PRs were scored (x0.8)."


def _records(data: Mapping, key: str, kind: type) -> list:
    return [r.from_dict(kind, item) for item in data.get(key) or ()]


def facts(inv: Investigation, data: Mapping, statuses: Sequence[cov.SourceStatus]) -> dict:
    w = inv.windows
    series = _records(data, "series", r.Series)
    metrics = [m for m in (scoring.deviation(s, w) for s in series) if m]
    logs = scoring.log_spikes(_records(data, "log_lines", r.LogLine), w)
    errors = scoring.error_signals(_records(data, "issues", r.Issue), w)
    changes = _records(data, "changes", r.Change)
    repo = str(data.get("repo") or inv.repo)
    suspects = scoring.rank_suspects(changes, w, inv.service, repo, errors, logs)
    later = [c for c in changes if (t := parse_time(c.time)) is not None and t > w.t0]
    similar = scoring.similar(inv.incident.title, inv.service, _records(data, "past", r.PastIncident), w,
                              inv.incident.id)
    notes = []
    code_ok = any(s.source == "code" and s.state == "ok" for s in statuses)
    if code_ok and not suspects:
        notes.append(NO_SUSPECT)
    if changes and not any(c.kind == "deploy" for c in changes):
        notes.append(NO_DEPLOYS)
    dropped = sum(int(data.get(k) or 0) for k in ("dropped_logs", "dropped_issues", "dropped_changes",
                                                    "dropped_past"))
    return {
        "incident": r.to_dict(inv.incident), "t0": iso(w.t0), "service": inv.service, "repo": repo,
        "alerting": inv.alerting,
        "metrics": [{"label": m.label, "onset": m.onset, "peak_ratio": m.peak_ratio, "base": m.base} for m in metrics],
        "log_spikes": [{"sample": g.sample, "hot_count": g.hot_count, "base_count": g.base_count, "onset": g.onset}
                       for g in logs],
        "errors": [{"id": e.issue.id, "title": e.issue.title, "kind": e.kind, "onset": e.onset,
                    "link": e.issue.link, "count": e.issue.count, "files": list(e.issue.files)} for e in errors],
        "suspects": [{**r.to_dict(s.change), "score": s.score, "delta_min": s.delta_min, "parts": s.parts,
                      "evidence": list(s.evidence)} for s in suspects],
        "later_changes": [r.to_dict(c) for c in later],
        "similar": [{**r.to_dict(s.past), "sim": s.sim, "same_service": s.same_service} for s in similar],
        "runbook": str(data.get("runbook") or inv.incident.runbook_url or ""),
        "coverage": list(cov.lines(statuses)),
        "notes": notes,
        "dropped": dropped,
    }
