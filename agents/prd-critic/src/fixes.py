"""The fix table: one fixed line of advice per gap kind. The critic points; it never rewrites, and the
model never writes a fix."""

from __future__ import annotations

SECTION_FIX = {
    "problem": "Add a Problem section: who has the problem, how often, and the evidence.",
    "users": "Add a Users section naming the primary user roles this is for.",
    "goals": "Add Goals: the outcomes this release must achieve.",
    "non_goals": "Add Non-goals: what this release deliberately will not do.",
    "requirements": "Add Requirements as separate lines, one observable behaviour each.",
    "metrics": "Add Success metrics: target, baseline and time window for each goal.",
    "open_questions": "Add Open questions, each with an owner and a decide-by date.",
}
FIX = {
    "not_testable": "Say what a tester would check: observable result and limit.",
    "ambiguous": "Replace the vague term with a number or an observable behaviour.",
    "lexicon": "Replace the vague term with a number or an observable behaviour.",
    "no_actor": "Name who does it: a user role or the system.",
    "not_single": "Split it into one requirement per behaviour.",
    "solution": "State the need; move the implementation choice to the design doc.",
    "few_requirements": "List the requirements as separate, testable lines.",
    "no_target": "State target, baseline and window (from X to Y by a date).",
    "no_window": "Add the time window or date the target must be met by.",
    "no_baseline": "Add the current value (baseline) the target is measured against.",
    "unlinked": "Add a metric that measures each goal.",
    "unowned": "Give the question an owner and a decide-by date.",
    "placeholder": "Resolve the placeholder or move it to Open questions with an owner.",
}


def section_fix(key: str) -> str:
    return SECTION_FIX[key]


def fix(kind: str) -> str:
    return FIX[kind]
