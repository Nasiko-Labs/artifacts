"""One blast-radius run: parse (done by the caller) → risk → select → search the code host → assemble
`affected[]` → narrate → Analysis. Every fact is code; the model only narrates."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

import affected
import prompts
import risk
import search_plan
import steps
from analysis import Analysis, ChangeRow, rank_changes
from deps import TurnDeps
from narrate import narrate
from plan_model import ParsedPlan
from records import Hit

log = logging.getLogger("terraform_blast_radius.pipeline")

NO_REPO_NOTE = "dependents not searched in the code host: no repository was given"
DYNAMIC_NOTE = "dynamic references (for_each, computed names) can't be traced by text search"


async def analyze(deps: TurnDeps, plan: ParsedPlan, repo: str, extras: Sequence[str], env: str,
                  grep: Sequence[Hit] = ()) -> Analysis:
    changing = plan.changing
    risks = {c.address: risk.assess(c) for c in changing}
    rows = rank_changes([ChangeRow.build(c, risks[c.address]) for c in changing])
    selected, eligible = search_plan.select(changing, risks)
    levels = {address: r.level for address, r in risks.items()}
    by_address = {c.address: c for c in selected}
    extras = tuple(r for r in extras if r != repo)[:search_plan.MAX_EXTRA_REPOS]
    if repo:
        needles = search_plan.plan(selected, repo, extras)
        found = await steps.find(deps, needles, by_address, levels, grep)
    else:
        needles = search_plan.plan(selected, affected.PASTED, ())
        found = await steps.find(deps, needles, by_address, levels, grep, search=False)
    notes = _notes(plan, found, repo, bool(selected))
    checks = tuple(dict.fromkeys(ch for c in changing for ch in risks[c.address].checks))
    result = Analysis(repo=repo, extra_repos=extras, env=env, format=plan.format, stated=plan.stated,
                      parsed=plan.counts(), mismatch=plan.mismatch, changes=rows,
                      searched=tuple(c.address for c in selected), eligible=eligible, affected=found.affected,
                      checks=checks, notes=notes, code_host=found.searched, complete=found.complete,
                      codeowners=found.codeowners)
    if not selected:
        return result
    narrative, breaks = await narrate(deps, result)
    return replace(result, narrative=narrative, breaks=breaks)


def _notes(plan: ParsedPlan, found: steps.Dependents, repo: str, any_selected: bool) -> tuple[str, ...]:
    notes = []
    if plan.mismatch:
        parsed = plan.counts()
        said = ", ".join(f"{plan.stated.get(k, 0)} to {k}" for k in ("add", "change", "destroy"))
        got = ", ".join(f"{parsed[k]} to {k}" for k in ("add", "change", "destroy"))
        notes.append(f"plan parsed partially: its summary says {said}, I read {got}")
    if any_selected and not repo:
        notes.append(NO_REPO_NOTE)
    notes += found.notes
    if found.dropped:
        notes.append(f"{found.dropped} dependent(s) dropped: not found verbatim in the tool output")
    unread = sum(1 for r in found.affected if not r.line)
    if unread:
        notes.append(f"line numbers not read for {unread} dependent(s)")
    unknown = sum(1 for r in found.affected if r.owner == "unknown")
    if unknown:
        notes.append(f"owner unknown for {unknown} dependent(s) (no CODEOWNERS rule or recent commits)")
    if found.searched:
        notes.append(DYNAMIC_NOTE)
    return tuple(dict.fromkeys(notes))


async def search_more(deps: TurnDeps, a: Analysis, repos: Sequence[str]) -> Analysis:
    """The follow-up "search <owner/repo>": the searched resources' identifiers in the new repositories."""
    new = tuple(r for r in repos if r not in (a.repo, *a.extra_repos))[:search_plan.MAX_EXTRA_REPOS]
    if not new:
        return replace(a, view="", notes=a.notes)
    needles = tuple(search_plan.Needle("identifier", c.identifier, r, c.address)
                    for c in a.changes if c.address in a.searched and c.identifier for r in new)
    if not needles:
        note = f"{', '.join(new)} not searched: no searched resource has a distinctive name or host in the plan"
        return replace(a, extra_repos=(*a.extra_repos, *new), notes=(*a.notes, note), view="")
    levels = {c.address: c.risk_level for c in a.changes}
    found = await steps.find(deps, needles, {}, levels)
    rows = affected.dedupe(affected.rank([*a.affected, *found.affected]))
    notes = tuple(dict.fromkeys((*a.notes, *found.notes)))
    if not found.searched:
        notes = tuple(dict.fromkeys((*notes, prompts.NO_CODE_HOST)))
    return replace(a, extra_repos=(*a.extra_repos, *new), affected=tuple(rows), notes=notes, view="",
                   complete=a.complete and found.complete)
