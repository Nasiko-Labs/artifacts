"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from pipeline import Outcome
from rank import MIN_COUNT, Story

HIGH_MIN_STORIES = 6
HIGH_MIN_LENSES = 3
LOW_MAX_PLAN_WORDS = 150


def failure_mode(rank: int, story: Story) -> dict:
    c = story.candidate
    return {"rank": rank, "title": c.title, "story": c.story, "root_assumption": c.root_assumption,
            "plan_line": c.line, "plan_ref": c.plan_ref, "likelihood": c.likelihood, "impact": c.impact,
            "score": story.score, "early_warning": c.early_warning, "mitigation": c.mitigation,
            "raised_by": list(story.raised_by)}


def confidence(outcome: Outcome, plan_words: int) -> str:
    count = len(outcome.stories)
    lenses = {lens for s in outcome.stories for lens in s.raised_by}
    if count < MIN_COUNT or len(lenses) <= 1 or not outcome.consolidate_ok or plan_words < LOW_MAX_PLAN_WORDS:
        return "low"
    if count >= HIGH_MIN_STORIES and len(lenses) >= HIGH_MIN_LENSES and outcome.voice_failures == 0:
        return "high"
    return "medium"


def needs_human(outcome: Outcome, level: str) -> bool:
    severe = any(s.candidate.likelihood == "high" and s.candidate.impact == "high" for s in outcome.stories)
    return level == "low" or severe


def build(outcome: Outcome, plan_words: int, plan_lines: int) -> dict:
    modes = [failure_mode(n, s) for n, s in enumerate(outcome.stories, start=1)]
    level = confidence(outcome, plan_words)
    if modes:
        summary = f"{len(modes)} failure modes; top risk: {modes[0]['title']}."
    else:
        summary = "No failure mode was specific enough to tie to a line of the plan."
    return {"summary": summary, "items": modes, "failure_modes": modes,
            "unstated_assumptions": [dict(a) for a in outcome.assumptions],
            "questions_for_the_team": list(outcome.questions),
            "confidence": level, "needs_human": needs_human(outcome, level),
            "plan_lines": plan_lines, "left_out": outcome.left_out}
