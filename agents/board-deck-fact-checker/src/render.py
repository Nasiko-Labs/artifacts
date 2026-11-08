"""Pure: the report, its envelope (`summary`, `items`, `confidence`, `needs_human`, `mismatches`,
`unverifiable`, `deck_inconsistencies`, `matched`, `coverage`, `rules`) and the markdown reply. Confidence and
needs_human are computed here from the checks, never taken from the model. Output is in slide order."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import compare
from records import Comparison

FLAGGED = ("mismatch", "unit_mismatch", "period_mismatch", "ambiguous")
HIGH_SHARE = 0.9
MEDIUM_SHARE = 0.6
_HUMAN = ("ambiguous", "unit_mismatch", "period_mismatch")
_STATUS_TEXT = {
    "unit_mismatch": "unit or scale mismatch",
    "period_mismatch": "period_mismatch",
    "ambiguous": "ambiguous: two source cells",
}


@dataclass(frozen=True)
class Report:
    title: str
    slide_count: int
    source: str
    comparisons: tuple[Comparison, ...]
    inconsistencies: tuple[dict, ...] = ()
    coverage: tuple[str, ...] = ()
    mode: str = "default"
    sheet_read: bool = False
    fully_read: bool = True

    def of(self, *statuses: str) -> tuple[Comparison, ...]:
        found = (c for c in self.comparisons if c.status in statuses)
        return tuple(sorted(found, key=lambda c: (c.claim.slide, c.claim.claim_id)))


def confidence(report: Report) -> str:
    total = len(report.comparisons)
    validated = sum(1 for c in report.comparisons if c.status != "unverifiable")
    share = validated / total if total else 0.0
    if not report.sheet_read or share < MEDIUM_SHARE:
        return "low"
    return "high" if share >= HIGH_SHARE and report.fully_read else "medium"


def needs_human(report: Report) -> bool:
    return confidence(report) != "high" or any(c.status in _HUMAN for c in report.comparisons)


def summary(report: Report) -> str:
    ok, rounded = len(report.of("ok", "ok_rounded")), len(report.of("ok_rounded"))
    return (f"{len(report.comparisons)} claims checked: matched {ok} ({rounded} within rounding), "
            f"mismatches {len(report.of(*FLAGGED))}, unverifiable {len(report.of('unverifiable'))}, "
            f"deck inconsistencies {len(report.inconsistencies)}.")


def envelope(report: Report) -> dict:
    mismatches = [c.entry() for c in report.of(*FLAGGED)]
    return {
        "summary": summary(report), "items": mismatches, "confidence": confidence(report),
        "needs_human": needs_human(report), "mismatches": mismatches,
        "unverifiable": [c.entry() for c in report.of("unverifiable")],
        "deck_inconsistencies": list(report.inconsistencies), "matched": len(report.of("ok", "ok_rounded")),
        "coverage": list(report.coverage), "rules": {**compare.RULES, "mode": report.mode},
        "claims": [c.claim.to_dict() for c in sorted(report.comparisons, key=lambda c: c.claim.slide)],
    }


def json_block(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"


def markdown(report: Report) -> str:
    source = report.source or "no source read"
    head = (f'**Board deck check: "{report.title}"**, {report.slide_count} slides, {len(report.comparisons)} '
            f"claims, source: {source}")
    counts = (f"Matched {len(report.of('ok', 'ok_rounded'))} ({len(report.of('ok_rounded'))} within rounding). "
              f"Mismatches {len(report.of(*FLAGGED))}. Unverifiable {len(report.of('unverifiable'))}. "
              f"Deck inconsistencies {len(report.inconsistencies)}. Confidence: {confidence(report)}.")
    lines = [head, counts]
    flagged = report.of(*FLAGGED)
    if flagged:
        lines += ["", "**Mismatches**", *(f"- {_mismatch(c)}" for c in flagged)]
    if report.inconsistencies:
        lines += ["", "**Deck inconsistencies**", *(f"- {_inconsistency(i)}" for i in report.inconsistencies)]
    unverifiable = report.of("unverifiable")
    if unverifiable:
        lines += ["", "**Unverifiable**", *(f'- Slide {c.claim.slide}: "{_q(c.claim.quote)}": {c.rule_note}'
                                             for c in unverifiable)]
    rounded = report.of("ok_rounded")
    if rounded:
        lines += ["", "**Within tolerance**", *(f"- {_within(c)}" for c in rounded)]
    if report.coverage:
        lines += ["", "**Coverage**", *(f"- {line}" for line in report.coverage)]
    lines += ["", RULES_LINE.format(mode=report.mode)]
    return "\n".join(lines)


RULES_LINE = ("Rules: rounding to half the last written digit; truncation accepted and labelled; period must match; "
              "growth and ratios recomputed; mode: {mode} (say \"strict\" or \"loose\" to change).")


def _within(c: Comparison) -> str:
    return (f'Slide {c.claim.slide}: "{_q(c.claim.quote)}" deck {c.deck_value}, sheet {c.source_value} '
            f"({c.source_ref}): {c.rule_note}")


def _mismatch(c: Comparison) -> str:
    where = f" ({c.source_ref})" if c.source_ref else ""
    link = f" [source]({c.source_url})" if c.source_url.startswith(("http://", "https://")) else ""
    head = f'Slide {c.claim.slide}: "{_q(c.claim.quote)}" deck {c.deck_value}, sheet {c.source_value}{where}'
    if c.status == "mismatch":
        sign = "-" if c.delta.startswith("-") else "+"
        relative = f", {sign}{c.relative_delta * 100:.1f} %" if c.relative_delta is not None else ""
        detail = f"{c.delta}{relative}, {c.materiality}. {_sentence(c.rule_note)}."
    else:
        detail = f"{_STATUS_TEXT.get(c.status, c.status)}: {c.rule_note}."
    warn = f" Warning: {'; '.join(c.warnings)}." if c.warnings else ""
    return f"{head}: {detail}{warn}{link}"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _inconsistency(item: dict) -> str:
    stated = "; ".join(f"slide {s['slide']} says {s['value']}" for s in item["statements"])
    return f"{item['label']} ({item['period']}): {stated}."


def _q(text: str, limit: int = 120) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def coverage_lines(lines: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(line for line in lines if line))
