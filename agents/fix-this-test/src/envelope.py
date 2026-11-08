"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields) from a
fix run. Every field is computed here: the patch by difflib, the counts from real runs, `verified` by the
verify run; only `explanation` comes from the model, and it is checked first."""

from __future__ import annotations

import checks
import patch
from pipeline import FixOutcome

EMPTY_COUNTS = {"failed": 0, "passed": 0, "errors": 0}
TEST_CHANGED = "The test file was edited because the model judged the test itself wrong; read that change carefully."
SPECIAL_CASING = "This change may be fitting the test's data instead of fixing the cause."
STEP_CAP = "Step limit reached; this is the state at that point."


def build(outcome: FixOutcome, originals: dict[str, str], failure: str, explanation: str,
          coverage: list[str]) -> dict:
    loop = outcome.loop
    finals = loop.finals if loop else dict(originals)
    diff = patch.unified(originals, finals)
    items = patch.stats(originals, finals)
    changed = sum(i["added"] + i["removed"] for i in items)
    before = outcome.before.counts() if outcome.before else dict(EMPTY_COUNTS)
    after = outcome.after.counts() if outcome.after else dict(EMPTY_COUNTS)
    counts = {*before.values(), *after.values(), changed, after["passed"] + after["failed"] + after["errors"]}
    text, dropped = checks.check_explanation(explanation, originals, failure, counts, outcome.verified)
    warnings = _warnings(outcome, changed, dropped, coverage)
    finished = bool(loop and loop.finished)
    changed_test, special = bool(loop and loop.changed_test), bool(loop and loop.special_casing)
    level = checks.confidence(verified=outcome.verified, mode=outcome.mode, warnings=warnings, changed=changed,
                              changed_test=changed_test, special_casing=special, finished=finished,
                              compiles=checks.compiles(finals), has_patch=bool(diff))
    return {"summary": _summary(outcome, items, after), "items": items, "patch": diff, "explanation": text,
            "verified": outcome.verified, "tests_before": before, "tests_after": after,
            "changed_test": changed_test, "warnings": warnings, "mode": outcome.mode,
            "targets": list(outcome.targets), "static_reason": outcome.static_reason,
            "special_casing_suspected": special, "dropped_explanation_sentences": dropped,
            "refused_edits": len(loop.refusals) if loop else 0,
            "confidence": level,
            "needs_human": checks.needs_human(verified=outcome.verified, level=level, changed_test=changed_test,
                                              special_casing=special)}


def flaky(outcome: FixOutcome, coverage: list[str]) -> dict:
    diagnosis = outcome.flaky
    counts = outcome.before.counts() if outcome.before else dict(EMPTY_COUNTS)
    if diagnosis.passes == diagnosis.runs:
        summary = f"Can't reproduce: the test passes {diagnosis.passes} of {diagnosis.runs} re-runs here."
    else:
        summary = f"Flaky: the test passed {diagnosis.passes} of {diagnosis.runs} re-runs here."
    causes = [{"cause": c, "quote": q} for c, q in diagnosis.causes]
    warnings = [*coverage, *([f"{diagnosis.dropped} suggested cause(s) dropped: their quotes are not in your input."]
                             if diagnosis.dropped else [])]
    return {"summary": summary, "items": causes, "patch": "", "explanation": "", "verified": False,
            "tests_before": counts, "tests_after": counts, "changed_test": False, "warnings": warnings,
            "mode": "sandbox", "targets": list(outcome.targets), "reruns_passed": diagnosis.passes,
            "reruns": diagnosis.runs, "confidence": "low", "needs_human": True}


def _warnings(outcome: FixOutcome, changed: int, dropped: int, coverage: list[str]) -> list[str]:
    loop = outcome.loop
    out = list(outcome.notes) + list(coverage)
    if loop and loop.changed_test:
        out.append(TEST_CHANGED)
    if loop and loop.special_casing:
        out.append(SPECIAL_CASING)
    if changed > checks.HIGH_MAX_CHANGED_LINES:
        out.append(f"Large change: {changed} lines.")
    if loop and not loop.finished and changed:
        out.append(STEP_CAP)
    if dropped:
        out.append(f"{dropped} sentence(s) of the explanation were dropped: they named things not in your input "
                   "or claimed a result the re-run did not show.")
    return out


def _summary(outcome: FixOutcome, items: list[dict], after: dict) -> str:
    paths = ", ".join(i["path"] for i in items) or "no file"
    total = after["passed"] + after["failed"] + after["errors"]
    if outcome.verified:
        return f"Fixed and verified ({paths}): {after['passed']} of {total} tests pass on re-run."
    if not items:
        return "No fix found: the code is unchanged."
    if outcome.mode == "static":
        return f"Suggested fix ({paths}), not verified: {outcome.static_reason}."
    return f"Suggested fix ({paths}), not verified: {after['failed'] + after['errors']} of {total} tests still fail."
