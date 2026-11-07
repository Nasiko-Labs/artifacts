"""Pure: confidence per finding, overall confidence and needs_human, computed from the checked evidence only.
The model's own idea of confidence is never asked for or read.

Per finding:
- low    conflicts outnumber agreements; or one source and nothing specific (opinion / prediction /
         recommendation, or a fact without a number, date or name).
- high   >= 2 independent sources agree, at least one quote has a number or date (or the claim is a method),
         no conflict, and not every claim is a view.
- medium agreement whose claims are all views; agreement with conflicts that do not outnumber it; or one
         source with a fact / statistic / method whose quote has a number, date or name.
Overall: low if 1 source, < 3 findings, the map window closed early or > 40% of claims were unclustered;
high if >= 3 independent sources and >= 60% of findings are high or medium; else medium.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

import grounding as g
from model import VIEW_KINDS, Claim, Finding
from relate import Relations

HIGH_MIN_SOURCES = 3
HIGH_MIN_SHARE = 0.6
LOW_MIN_FINDINGS = 3
LOW_MAX_UNCLUSTERED = 0.4
SPECIFIC_KINDS = frozenset({"fact", "statistic", "method"})
_NAME = re.compile(r"(?<=[a-z,;:] )[A-Z][a-zA-Z]{2,}")


def has_number(claim: Claim) -> bool:
    return bool(g.numbers(claim.quote))


def has_name(claim: Claim) -> bool:
    return bool(_NAME.search(claim.quote))


def score(finding: Finding, claims: Sequence[Claim], relations: Relations | None,
          grouping: Mapping[str, str]) -> Finding:
    rel = relations or Relations()
    by_id = {c.id: c for c in claims}
    independent_agree = [p for p in rel.agree if grouping.get(by_id[p[0]].source_id) != grouping.get(by_id[p[1]].source_id)]
    agrees, conflicts = len(independent_agree), len(rel.conflicts)
    is_view = all(c.kind in VIEW_KINDS for c in claims)
    if conflicts and conflicts > agrees:
        level = "low"
    elif agrees:
        strong = any(has_number(c) or c.kind == "method" for c in claims)
        level = "high" if not conflicts and not is_view and strong else "medium"
    else:
        specific = any(c.kind in SPECIFIC_KINDS and (has_number(c) or has_name(c)) for c in claims)
        level = "medium" if specific else "low"
    agreement = "conflict" if conflicts else "agree" if agrees else "single"
    conflict = ({"a": rel.conflicts[0][0], "b": rel.conflicts[0][1], "difference": rel.conflicts[0][2]}
                if conflicts else None)
    source_ids = tuple(dict.fromkeys(c.source_id for c in claims))
    return replace(finding, source_ids=source_ids, agreement=agreement, conflict=conflict, confidence=level,
                   agree_pairs=agrees, conflict_pairs=conflicts, is_view=is_view)


def overall(findings: Sequence[Finding], independent_sources: int, timed_out: bool, unclustered: int,
            claims: int) -> str:
    if (independent_sources <= 1 or len(findings) < LOW_MIN_FINDINGS or timed_out
            or (claims and unclustered / claims > LOW_MAX_UNCLUSTERED)):
        return "low"
    solid = sum(f.confidence in ("high", "medium") for f in findings)
    if independent_sources >= HIGH_MIN_SOURCES and solid / len(findings) >= HIGH_MIN_SHARE:
        return "high"
    return "medium"


def needs_human(level: str, findings: Sequence[Finding], claims: Mapping[str, Claim], not_read_for_time: bool) -> bool:
    statistic_conflict = any(
        f.conflict and any(claims[i].kind == "statistic" for i in (f.conflict["a"], f.conflict["b"]) if i in claims)
        for f in findings)
    return level == "low" or statistic_conflict or not_read_for_time
