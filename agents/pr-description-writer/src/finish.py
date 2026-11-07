"""From the model's draft to the reply: code checks, the title, the test plan, the markdown, the envelope and
the coverage notes. Shared by the first draft and every restyle, so both pass the same checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

import envelope as env
import render
import risk
import steps
import title
from checks import Ground, check_draft
from facts import Facts, decide_type
from inputs import Style
from llm import TurnDeps
from template import Heading

MAX_WHY_CHARS = 600
SHORT_SUMMARY_SENTENCES = 2
SHORT_RISK_NOTES = 2
MAX_RISK_NOTES = 5
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Job:
    model: dict
    facts: Facts
    diff: str
    context: str
    headings: tuple[Heading, ...]
    style: Style
    grouped: bool = False
    skipped: tuple[str, ...] = ()
    diff_chars: int = 0
    wants_json: bool = False


@dataclass(frozen=True)
class Finished:
    reply: str
    envelope: dict
    draft: dict


def _shorten(draft: dict) -> dict:
    summary = " ".join(_SENTENCE.split(draft["summary"])[:SHORT_SUMMARY_SENTENCES])
    return {**draft, "summary": summary, "reviewer_notes": [], "risk_notes": draft["risk_notes"][:SHORT_RISK_NOTES]}


def _summary_from_facts(facts: Facts) -> str:
    where = f"`{facts.scope}`" if facts.scope else "the codebase"
    return f"Changes {facts.files} file{'s' if facts.files != 1 else ''} in {where}."


def _why(checked: dict, context: str) -> str | None:
    if not context:
        return None
    if checked.get("why_from_user"):
        return render.clean(checked["why_from_user"], context)
    text = render.clean(context, context)
    return text if len(text) <= MAX_WHY_CHARS else text[:MAX_WHY_CHARS].rsplit(" ", 1)[0] + " …"


def _coverage(job: Job, report, title_notes: list[str]) -> list[str]:
    facts, notes = job.facts, []
    if facts.stat_only:
        notes.append("This is a `--stat` listing only, so the description is shallow: paste the full diff for a "
                     "real one.")
    if job.grouped:
        notes.append(f"The diff is {job.diff_chars:,} characters, so I summarised it in groups of files.")
    if job.skipped:
        notes.append("Not summarised (size cap): " + ", ".join(f"`{p}`" for p in job.skipped[:10])
                     + (" and more" if len(job.skipped) > 10 else "") + ".")
    if facts.generated:
        notes.append("Generated or lock files are listed by name only: "
                     + ", ".join(f"`{p}`" for p in facts.generated[:10]) + ".")
    if report.dropped:
        notes.append(f"I dropped {report.dropped} item{'s' if report.dropped != 1 else ''} that named something "
                     "not in your diff.")
    if report.fixes:
        notes.append(f"I removed {report.fixes} sentence{'s' if report.fixes != 1 else ''} or reference"
                     f"{'s' if report.fixes != 1 else ''} I couldn't ground in your input.")
    return notes + title_notes


async def finish(deps: TurnDeps, job: Job) -> Finished:
    facts = job.facts
    checked, report = check_draft(job.model, Ground(job.diff, job.context, facts), facts, job.style.short)
    checked["risk_notes"] = risk.code_notes(facts.surfaces, checked["risk_notes"], MAX_RISK_NOTES)
    if job.style.short:
        checked = _shorten(checked)
    if not checked["summary"]:
        checked["summary"] = _summary_from_facts(facts)
    change_type, decided = decide_type(facts, job.model.get("type"))
    head = title.prefix(change_type, facts.scope, bool(facts.breaking), job.style.conventional)
    full_title, title_notes = await title.finish(deps, job.model["subject"], head, checked["summary"],
                                                 list(facts.paths), job.style.conventional, facts.scope)
    test_plan = steps.plan(steps.code_steps(facts), checked["test_steps"])
    allowed = job.diff + "\n" + job.context
    body = render.sections(checked, facts, _why(checked, job.context), test_plan, steps.tested_line(facts), allowed)
    markdown = render.description(checked, facts, body, job.headings, allowed)
    run = env.Run(why_given=bool(job.context), grouped=job.grouped, skipped=job.skipped,
                  repaired=deps.budget.repairs_left == 0, type_decided=decided)
    coverage = _coverage(job, report, title_notes)
    envelope = env.build(full_title, markdown, checked, facts, test_plan, report, run, coverage)
    return Finished(render.reply(envelope, coverage, job.wants_json), envelope, checked)
