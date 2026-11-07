"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `tests_code`,
`edge_cases_covered`, `edge_cases_not_covered`, `framework`). Coverage, confidence and needs_human are
computed here from verified claims and checks, never taken from the model."""

from __future__ import annotations

import re

from assemble import Assembled
from census import Census
from checks import MOCKED
from write import Outcome

MEDIUM_MIN_RATIO = 0.5


def coverage(census: Census, tests) -> tuple[list[str], list[str]]:
    covered = {i for t in tests for i in t.covers}
    yes = [f"{c.callable}: {c.label}" for c in census.cases if c.id in covered]
    no = [f"{c.callable}: {c.label}" for c in census.cases if c.id not in covered]
    return yes, no


def mocked(census: Census, tests) -> tuple[list[str], list[str]]:
    done, missing = [], []
    for c in census.selected:
        mine = [t for t in tests if t.callable == c.name]
        if not mine or not c.externals:
            continue
        target = done if any(MOCKED.search(t.code) for t in mine) else missing
        target += [f"{ext} in {c.name}" for ext in c.externals]
    return done, missing


def confidence(census: Census, tests, outcome: Outcome, assembled: Assembled) -> str:
    total = len(census.cases)
    covered = len({i for t in tests for i in t.covers})
    ratio = covered / total if total else 1.0
    reasoned = sum(t.basis == "reasoned" for t in tests)
    unsupported = any(c.unsupported for c in census.selected)
    if ratio < MEDIUM_MIN_RATIO or outcome.repair_failed or not assembled.file_ok or unsupported or not tests:
        return "low"
    if ratio == 1.0 and not outcome.repaired and reasoned == 0:
        return "high"
    return "medium"


def build(census: Census, assembled: Assembled, outcome: Outcome, framework: str, notes: list[str]) -> dict:
    tests = list(assembled.tests)
    yes, no = coverage(census, tests)
    done, missing = mocked(census, tests)
    reasoned = sum(t.basis == "reasoned" for t in tests)
    level = confidence(census, tests, outcome, assembled)
    callables = list(dict.fromkeys(t.callable for t in tests))
    summary = (f"{len(tests)} tests for {len(callables)} callable{'s' if len(callables) != 1 else ''}, "
               f"{len(yes)} of {len(census.cases)} edge cases covered; {reasoned} test"
               f"{'s' if reasoned != 1 else ''} assert reasoned values.")
    return {
        "summary": summary,
        "items": [{"callable": t.callable, "name": t.name, "covers": list(t.covers), "basis": t.basis} for t in tests],
        "tests_code": assembled.code if tests else "",
        "edge_cases_covered": yes, "edge_cases_not_covered": no,
        "framework": framework, "language": census.lang,
        "confidence": level,
        "needs_human": reasoned > 0 or outcome.suspected_bug is not None or level == "low",
        "callables": callables, "reasoned_tests": reasoned,
        "mocked": done, "not_mocked": missing,
        "suspected_bug": outcome.suspected_bug,
        "dropped": [{"name": n, "reason": r} for n, r in outcome.dropped],
        "claims_removed": outcome.claims_removed, "suspected_bugs_dropped": outcome.bugs_dropped,
        "not_tested": list(census.remaining), "over_cap": list(assembled.overflow),
        "placeholder_import": assembled.placeholder_import, "notes": notes,
        "branches": {c.name: c.branches for c in census.selected},
    }


def fence_lang(lang: str) -> str:
    return re.sub(r"[^a-z]", "", lang) or "text"
