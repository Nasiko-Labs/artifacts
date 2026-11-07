"""Pure: the markdown reply, rendered in code from the digest and the checked narrative. JSON mode returns the
envelope as a fenced block instead (MAF reads only text parts)."""

from __future__ import annotations

import json
from collections.abc import Sequence

import fmt
from checks import cell, clean_text
from digest import Digest, Line
from envelope import Coverage, Narration
from variance import ABSOLUTE_SHARE, HUNDRED, MIN_SHARE_OF_TOTAL

EMPTY = "None found."
HEADER = "| Line | Budget | Actual | Variance | % | F/U | Driver |\n|---|---|---|---|---|---|---|"
TITLES = {"U": "Biggest unfavourable variances", "F": "Biggest favourable variances", "": "Biggest variances"}


def as_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"


def render(d: Digest, narration: Narration, coverage: Coverage, source: str, table_only: bool = False,
           notes: Sequence[str] = ()) -> str:
    blocks = [] if table_only else [_narrative(d, narration, source)]
    blocks += [_table(d, source), _meta(d), *_caveats(d), *_coverage(coverage), *notes]
    return "\n\n".join(b for b in blocks if b)


def _narrative(d: Digest, narration: Narration, source: str) -> str:
    title = f"**Narrative ({cell(d.period, source)})**" if d.period else "**Narrative**"
    return f"{title}\n{clean_text(narration.text, source)}"


def _row(ln: Line, source: str) -> str:
    i = ln.item
    driver = f"{cell(i.driver, source)} ({i.driver_source})" if i.driver else "driver not provided"
    flag = {"F": "F", "U": "U"}.get(i.direction, "-")
    return (f"| {cell(i.name, source)} | {ln.budget} | {ln.actual} | {ln.signed} | {ln.signed_pct} | {flag} | "
            f"{driver} |")


def _table(d: Digest, source: str) -> str:
    title = f"**{TITLES[d.analysis.options.only or '']}**"
    if not d.lines:
        return f"{title}\n{EMPTY} No line crossed the materiality threshold."
    rows = [_row(ln, source) for ln in d.lines]
    extra = [ln for ln in d.offsets if ln not in d.lines]
    if extra:
        rows += [_row(ln, source) for ln in extra]
    return f"{title}\n{HEADER}\n" + "\n".join(rows)


def _meta(d: Digest) -> str:
    a = d.analysis
    t = fmt.plain_number(a.options.threshold)
    lines = [f"Sign convention: {a.sign_convention}"]
    if a.reconciled:
        lines.append("Line items sum to the table total.")
    if a.concentration is not None:
        lines.append(f"The top three lines account for {fmt.pct(a.concentration)} of the gross variance.")
    if a.prior_change is not None:
        word = "above" if a.prior_change >= 0 else "below"
        lines.append(f"Total actual is {fmt.pct(abs(a.prior_change))} {word} the prior period.")
    lines.append(f"Material = at least {t}% and {fmt.plain_number(MIN_SHARE_OF_TOTAL * HUNDRED)}% of total budget, "
                 f"or {fmt.plain_number(ABSOLUTE_SHARE * HUNDRED)}% of total budget on its own.")
    return "_(" + " ".join(lines) + ")_"


def _caveats(d: Digest) -> list[str]:
    return [f"**Check:** {c}" for c in d.analysis.caveats]


def _coverage(c: Coverage) -> list[str]:
    notes = []
    if c.skipped:
        notes.append(f"{c.skipped} row(s) with n/a in budget or actual were skipped.")
    if c.unparseable:
        notes.append(f"{c.unparseable} row(s) I couldn't read as numbers were skipped.")
    if c.blanks:
        notes.append(f"{c.blanks} blank or '-' cell(s) were counted as 0.")
    if c.capped:
        notes.append(f"Only the first 200 line items were analysed; {c.capped} more were left out.")
    return notes
