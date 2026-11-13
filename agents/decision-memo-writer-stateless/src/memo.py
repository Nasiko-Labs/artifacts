"""Pure: the memo's markdown, in a fixed order, capped at MAX_WORDS by code trimming in a fixed order:
risks beyond 3, pros/cons beyond 2 per option, rationale beyond 2, then context to its first 2 sentences.
"shorter" applies the tight caps directly."""

from __future__ import annotations

import re
from dataclasses import dataclass

import criteria as crit
from brief import Brief
from grounding import JUDGEMENT
from text import clean, words

MAX_WORDS = 450
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PLACEHOLDER = re.compile(r"\[(?:figure|date|owner|threshold|trigger|decision|mitigation)\?\]")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Caps:
    risks: int = 4
    points: int = 4
    rationale: int = 3
    sentences: int = 0  # 0 = the whole context


TRIM_STEPS = (Caps(risks=3), Caps(risks=3, points=2), Caps(risks=3, points=2, rationale=2),
              Caps(risks=3, points=2, rationale=2, sentences=2))
TIGHT = Caps(risks=2, points=1, rationale=1, sentences=1)


def names(brief: Brief) -> dict[str, str]:
    return {o.key: o.name for o in brief.options}


def _cell(points: list[dict], cap: int) -> str:
    shown = [clean(p["text"]) + (" (j)" if p["basis"] == JUDGEMENT else "") for p in points[:cap]]
    return "; ".join(shown) or "-"


def _table(brief: Brief, analysis: dict, basis: dict, caps: Caps) -> list[str]:
    scored = bool(basis["scores"])
    head = "| Option | Pros | Cons |" + (" Score |" if scored else "")
    rule = "|---|---|---|" + ("---|" if scored else "")
    rows = []
    for o in brief.options:
        label = f"{o.key}. {clean(o.name)}" + (" (added by me)" if o.added_by_me else "")
        row = f"| {label} | {_cell(analysis['pros'][o.key], caps.points)} | {_cell(analysis['cons'][o.key], caps.points)} |"
        rows.append(row + (f" {basis['scores'][o.key]} |" if scored else ""))
    return [head, rule, *rows]


def recommendation_line(brief: Brief, analysis: dict, memo: dict, basis: dict) -> str:
    n, rec, alt = names(brief), basis["option"], basis["runner_up"]
    label = "Recommendation (your choice)" if basis["kind"] == "override" else "Recommendation (draft)"
    head = f"**{label}: {rec}, {clean(n.get(rec, rec))}.**"
    if basis["close_call"] and alt:
        unless = clean(memo["triggers"][0]["trigger"]).rstrip(".") if memo["triggers"] else "[trigger?]"
        head = (f"**{label}: close call ({rec} {basis['scores'][rec]} vs {alt} {basis['scores'][alt]}). "
                f"{rec}, {clean(n.get(rec, rec))}, unless {unless}.**")
    parts = [head, _basis_text(brief, basis)]
    if basis["kind"] in ("preference", "override") and alt:
        cons = analysis["cons"].get(rec) or []
        main_con = f"Main con: {clean(cons[0]['text'])}. " if cons else ""
        parts.append(f"{main_con}Best alternative: {alt}, {clean(n.get(alt, alt))}.")
    if memo["recommendation"]:
        parts.append(clean(memo["recommendation"]))
    return " ".join(parts)


def _basis_text(brief: Brief, basis: dict) -> str:
    rec, alt, kind = basis["option"], basis["runner_up"], basis["kind"]
    if kind == "override":
        return "Basis: your choice."
    if kind == "preference":
        return "Basis: your stated preference."
    if kind == "criteria":
        scores = f"; {rec} {basis['scores'][rec]} vs {alt} {basis['scores'][alt]}" if alt else ""
        return f"Basis: your criteria ({crit.describe(brief.criteria)}){scores}."
    if brief.constraints:
        return ("Basis: no criteria given, so I weighted against your constraints: "
                + "; ".join(clean(c) for c in brief.constraints) + ".")
    return "Basis: no criteria or constraints given, so I picked the option with the fewest cons."


def _risks(brief: Brief, analysis: dict, basis: dict, cap: int) -> list[str]:
    rec = basis["option"]
    ordered = sorted(analysis["risks"], key=lambda r: ((r["option"] or rec) != rec, SEVERITY_ORDER[r["severity"]]))
    lines = []
    for r in ordered[:cap]:
        tag = f"[{r['option']}] " if r["option"] and r["option"] != rec else ""
        judged = " (judgement)" if r["basis"] == JUDGEMENT else ""
        text, fix = clean(r["text"]).rstrip("."), clean(r["mitigation"]).rstrip(".")
        lines.append(f"- {tag}{text}{judged} ({r['severity']}). Mitigation: {fix}.")
    return lines


def _triggers(brief: Brief, memo: dict) -> list[str]:
    n = names(brief)
    lines = [f"- {clean(t['trigger']).rstrip('.')} -> switch to {t['would_switch_to']}, {clean(n.get(t['would_switch_to'], ''))}"
             if t["would_switch_to"] else f"- {clean(t['trigger'])}" for t in memo["triggers"]]
    if len(lines) < 3:
        lines.append("- Add what would change your mind: [trigger?]")
    return lines


def _context(text: str, sentences: int) -> str:
    return " ".join(_SENTENCES.split(clean(text))[:sentences]) if sentences else clean(text)


def body(brief: Brief, analysis: dict, memo: dict, basis: dict, caps: Caps) -> str:
    blocks = [f"# Decision memo: {clean(brief.decision)}"]
    if brief.needed_by:
        blocks.append(f"Decision needed by: {clean(brief.needed_by)}")
    blocks += ["**Context**\n" + (_context(memo["context"], caps.sentences) or "[context?]"),
               "**Options**\n" + "\n".join(_table(brief, analysis, basis, caps))
               + ("\n\n_(j) = my judgement, not from your brief._" if "(j)" in "".join(_table(brief, analysis, basis, caps))
                  else ""),
               recommendation_line(brief, analysis, memo, basis)]
    if memo["rationale"][:caps.rationale]:
        blocks.append("**Why**\n" + "\n".join(f"{i}. {clean(r['text'])}"
                                             for i, r in enumerate(memo["rationale"][:caps.rationale], start=1)))
    risks = _risks(brief, analysis, basis, caps.risks)
    if risks:
        blocks.append("**Risks and mitigations**\n" + "\n".join(risks))
    blocks.append("**What would change this decision**\n" + "\n".join(_triggers(brief, memo)))
    return "\n\n".join(blocks)


def count(markdown: str) -> int:
    return words(re.sub(r"[|#*_-]+", " ", markdown))


def build(brief: Brief, analysis: dict, memo: dict, basis: dict, shorter: bool = False) -> tuple[str, list[str]]:
    """(the memo markdown, notes about trimming)."""
    if shorter:
        return body(brief, analysis, memo, basis, TIGHT), []
    text = body(brief, analysis, memo, basis, Caps())
    first = count(text)
    for caps in TRIM_STEPS:
        if count(text) <= MAX_WORDS:
            break
        text = body(brief, analysis, memo, basis, caps)
    final = count(text)
    return text, ([f"Memo is {final} words, trimmed from {first}."] if final < first else [])


def open_items(markdown: str) -> list[str]:
    return sorted(set(PLACEHOLDER.findall(markdown)))
