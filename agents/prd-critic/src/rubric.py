"""Pure: the rubric's gaps, the score and its band. Every number here is code; the model never sees or sets one.

The score is 100 minus the weights of the final gap list: a missing or empty section costs its SECTION_POINTS, a
metric its measurability points, a requirement gap its SEVERITY_POINTS, a placeholder PLACEHOLDER_POINTS per
marker; caps apply per group. Model flags are normalised before they make a gap (a "not testable" flag needs a
vague word to point at; the lexicon names the word first), and the same requirement with the same issue is one
gap, so the flag noise between runs moves the score by at most a minor gap."""

from __future__ import annotations

import re
from dataclasses import dataclass

import fixes
import lexicon
from metrics import MetricCheck, clean
from requirements import Flags, Requirement, is_must
from scoring import SEVERITY_POINTS, Finding, deductions_of, dedupe
from sections import LABELS, REQUIRED, Layout

SECTION_POINTS = {"problem": 12, "requirements": 12, "metrics": 10, "users": 8, "goals": 8, "non_goals": 5,
                  "open_questions": 3}
BLOCKER_SECTIONS = frozenset({"problem", "requirements", "metrics"})
FEW_REQS_POINTS, FEW_REQS_MIN, FEW_REQS_PRD_WORDS = 5, 3, 300
UNLINKED_POINTS, PLACEHOLDER_POINTS, FREE_UNOWNED = 4, 2, 3
BANDS = ((90, "ready for review"), (70, "needs a pass"), (50, "needs work"), (0, "not ready"))
SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}
_OWNER = re.compile(r"\bowner\b|\bowned by\b|\bassigned\b|@\w|\(\s*[A-Z][a-z]+(?: [A-Z][a-z]+)?\s*\)|"
                    r"[-–—]\s*[A-Z][a-z]+(?: [A-Z]\.?)?\s*$|\bdecide by\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\bTBD\b|\bTODO\b|\bXXX\b|\[\?\]|\?\?\?")
_STOP = frozenset(["the", "and", "for", "with", "from", "that", "this", "will", "into", "each", "have", "more", "less", "than", "over", "under", "their", "they", "when", "what", "which", "who", "make", "made", "able", "also", "both", "such", "only", "very", "much", "most", "should", "would", "could", "must", "shall", "users", "user"])


@dataclass(frozen=True)
class Inputs:
    layout: Layout
    reqs: tuple[Requirement, ...]
    flags: dict[str, Flags]
    metrics: tuple[MetricCheck, ...]
    open_questions: tuple[tuple[int, str], ...]
    prd_words: int
    lenient: bool = False


def band(score: int) -> str:
    return next(name for floor, name in BANDS if score >= floor)


def gap(severity: str, category: str, section: str, quote: str | None, issue: str, fix: str) -> dict:
    return {"severity": severity, "category": category, "location": {"section": section, "quote": quote},
            "issue": issue, "fix": fix}


def _sections(inp: Inputs) -> list[Finding]:
    out = []
    for key in REQUIRED:
        section = inp.layout.sections.get(key)
        if section is not None and section.present:
            continue
        state = "empty" if section is not None else "missing"
        severity = "blocker" if key in BLOCKER_SECTIONS else "major"
        issue = f"{LABELS[key]} section is {state}" + (" (a heading with almost nothing under it)." if section else ".")
        out.append(Finding(gap(severity, "missing_section", LABELS[key], None, issue, fixes.section_fix(key)),
                           SECTION_POINTS[key], "section", f"{LABELS[key]} section {state}"))
    return out


def problems_of(req: Requirement, flags: Flags | None, lenient: bool) -> list[tuple[str, str]]:
    """(kind, words for the issue), normalised: the lexicon names the vague word first and the model's verified
    term only when the lexicon has none; "not testable" counts only when there is such a word to point at."""
    words = lexicon.hits(req.text, lenient)
    term = flags.ambiguous_term if flags else None
    vague = ("lexicon", f'vague: "{words[0]}"') if words else ("ambiguous", f'term: "{term}"') if term else None
    problems: list[tuple[str, str]] = []
    if flags and not flags.testable and vague:
        problems.append(("not_testable", "not testable"))
    if vague:
        problems.append(vague)
    if flags and not flags.single_behavior:
        problems.append(("not_single", "more than one behaviour"))
    if flags and not flags.has_actor:
        problems.append(("no_actor", "no actor"))
    return problems


def _requirement(req: Requirement, flags: Flags | None, lenient: bool) -> list[Finding]:
    out = []
    if flags and flags.contains_solution_detail:
        out.append(Finding(gap("minor", "solution_in_requirement", "Requirements", req.quote,
                               f"{req.id} prescribes the solution rather than the need.", fixes.fix("solution")),
                           SEVERITY_POINTS["minor"], "requirement", f'{req.id} "{req.text[:50]}": solution detail'))
    problems = problems_of(req, flags, lenient)
    if not problems:
        return out
    kinds = {k for k, _ in problems}
    severity = ("blocker" if "not_testable" in kinds and is_must(req) else
                "major" if kinds & {"not_testable", "ambiguous"} else "minor")
    said = "; ".join(w for _, w in problems)
    found = gap(severity, "vague_requirement", "Requirements", req.quote, f"{req.id}: {said}.",
                fixes.fix(problems[0][0]))
    return [Finding(found, SEVERITY_POINTS[severity], "requirement", f'{req.id} "{req.text[:50]}": {said}'), *out]


def _metric(m: MetricCheck) -> list[Finding]:
    if m.points == 0:
        return []
    missing = [w for w, ok in (("number", m.has_target), ("window", m.has_window), ("baseline", m.has_baseline))
               if not ok]
    kind = "no_target" if not m.has_target else "no_window" if not m.has_window else "no_baseline"
    severity = "blocker" if not m.measurable else "minor"
    found = gap(severity, "unmeasurable_metric", "Metrics", m.text, f"No {', '.join(missing)}.", fixes.fix(kind))
    return [Finding(found, m.points, "metric", f'Metric "{m.text[:50]}"')]


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z-]{3,}", (text or "").casefold()) if w not in _STOP}


def is_question(text: str) -> bool:
    return "?" in text or bool(re.search(r"\bTBD\b", text))


def _questions(inp: Inputs) -> list[Finding]:
    unowned = [t for _, t in inp.open_questions if is_question(t) and not _OWNER.search(t)]
    return [Finding(gap("minor", "unanswered_question", "Open questions", t, "Open question with no owner.",
                        fixes.fix("unowned")), 1 if n >= FREE_UNOWNED else 0, "question",
                    f'Unowned open question: "{t[:60]}"') for n, t in enumerate(unowned)]


def _placeholders(inp: Inputs) -> list[Finding]:
    skip = {n for n, _ in inp.layout.body("open_questions")}
    out = []
    for n, line in enumerate(inp.layout.lines, start=1):
        found = [] if n in skip else _PLACEHOLDER.findall(line)
        if found:
            out.append(Finding(gap("major", "placeholder", section_of(inp.layout, n), clean(line),
                                   f"Unresolved placeholder {found[0]}.", fixes.fix("placeholder")),
                               PLACEHOLDER_POINTS * len(found), "placeholder",
                               f"Placeholder {', '.join(found)} on L{n}"))
    return out


def _goal_link(inp: Inputs) -> list[Finding]:
    goals = [(n, t) for n, t in inp.layout.body("goals") if t.strip()]
    if not goals or not inp.metrics or not _present(inp.layout, "goals"):
        return []
    goal_words = set().union(*(content_words(t) for _, t in goals))
    if any(content_words(m.text) & goal_words for m in inp.metrics):
        return []
    found = gap("major", "unmeasurable_metric", "Metrics", goals[0][1].strip(), "No metric measures any stated goal.",
                fixes.fix("unlinked"))
    return [Finding(found, UNLINKED_POINTS, "goal_link", "No metric shares a word with any goal")]


def _few_requirements(inp: Inputs) -> list[Finding]:
    if len(inp.reqs) >= FEW_REQS_MIN or inp.prd_words < FEW_REQS_PRD_WORDS or not _present(inp.layout, "requirements"):
        return []
    found = gap("major", "vague_requirement", "Requirements", None, f"Only {len(inp.reqs)} requirement lines found.",
                fixes.fix("few_requirements"))
    return [Finding(found, FEW_REQS_POINTS, "few_requirements", f"Only {len(inp.reqs)} requirements parsed")]


def _present(lay: Layout, key: str) -> bool:
    section = lay.sections.get(key)
    return section is not None and section.present


def section_of(lay: Layout, n: int) -> str:
    for key, section in lay.sections.items():
        if section.start - (1 if section.rest else 0) <= n <= section.end:
            return LABELS[key]
    heading = max((h for h in lay.headings if h.line <= n), key=lambda h: h.line, default=None)
    return heading.title if heading else "PRD"


def evaluate(inp: Inputs) -> dict:
    """{score, band, deductions, gaps}: the score is 100 minus the capped weights of the deduplicated gaps; gaps
    sorted by severity, then by PRD order."""
    findings = [*_sections(inp)]
    for req in inp.reqs:
        findings += _requirement(req, inp.flags.get(req.id), inp.lenient)
    findings += _few_requirements(inp)
    for m in inp.metrics:
        findings += _metric(m)
    findings += [*_questions(inp), *_placeholders(inp), *_goal_link(inp)]
    findings = dedupe(findings)
    deductions = deductions_of(findings)
    score = max(0, 100 - sum(d["points"] for d in deductions))
    ordered = sorted(enumerate(f.gap for f in findings), key=lambda ig: (SEVERITY_ORDER[ig[1]["severity"]], ig[0]))
    return {"score": score, "band": band(score), "deductions": deductions, "gaps": [g for _, g in ordered]}
