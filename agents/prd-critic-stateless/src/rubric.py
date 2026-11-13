"""Pure: the deduction rubric, the gaps it implies, the score and its band. Every number here is code.
Model flags only switch individual requirement deductions on; the model never sees or sets a score."""

from __future__ import annotations

import re
from dataclasses import dataclass

import fixes
import lexicon
from metrics import MetricCheck, clean
from requirements import Flags, Requirement, is_must
from sections import LABELS, REQUIRED, Layout

SECTION_POINTS = {"problem": 12, "requirements": 12, "metrics": 10, "users": 8, "goals": 8, "non_goals": 5,
                  "open_questions": 3}
BLOCKER_SECTIONS = frozenset({"problem", "requirements", "metrics"})
REQ_CAP, METRIC_CAP, QUESTION_CAP, PLACEHOLDER_CAP = 30, 20, 10, 10
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


def _cap(group: list[tuple[int, str]], cap: int, label: str) -> list[dict]:
    rows = [{"points": p, "reason": r} for p, r in group if p]
    total = sum(r["points"] for r in rows)
    if total > cap:
        rows.append({"points": cap - total, "reason": f"{label} deductions capped at {cap}"})
    return rows


def _sections(inp: Inputs, gaps: list[dict]) -> list[tuple[int, str]]:
    out = []
    for key in REQUIRED:
        section = inp.layout.sections.get(key)
        if section is not None and section.present:
            continue
        state = "empty" if section is not None else "missing"
        out.append((SECTION_POINTS[key], f"{LABELS[key]} section {state}"))
        severity = "blocker" if key in BLOCKER_SECTIONS else "major"
        issue = f"{LABELS[key]} section is {state}" + (" (a heading with almost nothing under it)." if section else ".")
        gaps.append(gap(severity, "missing_section", LABELS[key], None, issue, fixes.section_fix(key)))
    return out


def _requirement(req: Requirement, flags: Flags | None, lenient: bool, gaps: list[dict]) -> tuple[int, str]:
    words = lexicon.hits(req.text, lenient)
    problems: list[tuple[str, int, str]] = []  # (kind, points, words for the issue)
    if flags and not flags.testable:
        problems.append(("not_testable", 3, "not testable"))
    if flags and flags.ambiguous_term:
        problems.append(("ambiguous", 2, f'term: "{flags.ambiguous_term}"'))
    elif words:
        problems.append(("lexicon", 2, f'vague: "{words[0]}"'))
    if flags and not flags.single_behavior:
        problems.append(("not_single", 2, "more than one behaviour"))
    if flags and not flags.has_actor:
        problems.append(("no_actor", 1, "no actor"))
    if flags and flags.contains_solution_detail:
        gaps.append(gap("minor", "solution_in_requirement", "Requirements", req.quote,
                        f"{req.id} prescribes the solution rather than the need.", fixes.fix("solution")))
    if not problems:
        return 0, ""
    kinds = {k for k, _, _ in problems}
    severity = ("blocker" if "not_testable" in kinds and is_must(req) else
                "major" if kinds & {"not_testable", "ambiguous"} else "minor")
    issue = f"{req.id}: " + "; ".join(w for _, _, w in problems) + "."
    gaps.append(gap(severity, "vague_requirement", "Requirements", req.quote, issue, fixes.fix(problems[0][0])))
    return sum(p for _, p, _ in problems), f'{req.id} "{req.text[:50]}": ' + "; ".join(w for _, _, w in problems)


def _metric(m: MetricCheck, gaps: list[dict]) -> int:
    if m.points == 0:
        return 0
    missing = [w for w, ok in (("number", m.has_target), ("window", m.has_window), ("baseline", m.has_baseline))
               if not ok]
    kind = "no_target" if not m.has_target else "no_window" if not m.has_window else "no_baseline"
    severity = "blocker" if not m.measurable else "minor"
    gaps.append(gap(severity, "unmeasurable_metric", "Metrics", m.text, f"No {', '.join(missing)}.", fixes.fix(kind)))
    return m.points


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z-]{3,}", (text or "").casefold()) if w not in _STOP}


def is_question(text: str) -> bool:
    return "?" in text or bool(re.search(r"\bTBD\b", text))


def _questions(inp: Inputs, gaps: list[dict]) -> list[tuple[int, str]]:
    unowned = [(n, t) for n, t in inp.open_questions if is_question(t) and not _OWNER.search(t)]
    for _, text in unowned:
        gaps.append(gap("minor", "unanswered_question", "Open questions", text, "Open question with no owner.",
                        fixes.fix("unowned")))
    extra = unowned[FREE_UNOWNED:]
    return [(1, f'Unowned open question: "{t[:60]}"') for _, t in extra]


def _placeholders(inp: Inputs, gaps: list[dict]) -> list[tuple[int, str]]:
    skip = {n for n, _ in inp.layout.body("open_questions")}
    out = []
    for n, line in enumerate(inp.layout.lines, start=1):
        if n in skip:
            continue
        found = _PLACEHOLDER.findall(line)
        if not found:
            continue
        quote = clean(line)
        gaps.append(gap("major", "placeholder", section_of(inp.layout, n), quote,
                        f"Unresolved placeholder {found[0]}.", fixes.fix("placeholder")))
        out += [(PLACEHOLDER_POINTS, f'Placeholder {f} on L{n}') for f in found]
    return out


def _goal_link(inp: Inputs, gaps: list[dict]) -> list[tuple[int, str]]:
    goals = [(n, t) for n, t in inp.layout.body("goals") if t.strip()]
    if not goals or not inp.metrics or not _present(inp.layout, "goals"):
        return []
    goal_words = set().union(*(content_words(t) for _, t in goals))
    if any(content_words(m.text) & goal_words for m in inp.metrics):
        return []
    quote = goals[0][1].strip()
    gaps.append(gap("major", "unmeasurable_metric", "Metrics", quote, "No metric measures any stated goal.",
                    fixes.fix("unlinked")))
    return [(UNLINKED_POINTS, "No metric shares a word with any goal")]


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
    """{score, band, deductions, gaps}: gaps sorted by severity, then by PRD order."""
    gaps: list[dict] = []
    deductions = [{"points": p, "reason": r} for p, r in _sections(inp, gaps)]
    req_rows = [_requirement(r, inp.flags.get(r.id), inp.lenient, gaps) for r in inp.reqs]
    deductions += _cap(req_rows, REQ_CAP, "Requirement")
    if len(inp.reqs) < FEW_REQS_MIN and inp.prd_words >= FEW_REQS_PRD_WORDS and _present(inp.layout, "requirements"):
        deductions.append({"points": FEW_REQS_POINTS, "reason": f"Only {len(inp.reqs)} requirements parsed"})
        gaps.append(gap("major", "vague_requirement", "Requirements", None,
                        f"Only {len(inp.reqs)} requirement lines found.", fixes.fix("few_requirements")))
    deductions += _cap([(_metric(m, gaps), f'Metric "{m.text[:50]}"') for m in inp.metrics], METRIC_CAP, "Metric")
    deductions += _cap(_questions(inp, gaps), QUESTION_CAP, "Open question")
    deductions += _cap(_placeholders(inp, gaps), PLACEHOLDER_CAP, "Placeholder")
    deductions += [{"points": p, "reason": r} for p, r in _goal_link(inp, gaps)]
    score = max(0, 100 - sum(d["points"] for d in deductions))
    ordered = sorted(enumerate(gaps), key=lambda ig: (SEVERITY_ORDER[ig[1]["severity"]], ig[0]))
    return {"score": score, "band": band(score), "deductions": deductions, "gaps": [g for _, g in ordered]}
