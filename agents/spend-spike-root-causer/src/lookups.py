"""The read side of one analysis: repositories for the team, code changes near the onset, the owner in chat,
then the one draft. Every lookup is optional: a missing or failing source becomes a coverage note, never a guess.

    discover ─ repos (user-named, else searched by team / service tag) ─┬─ none matched, some found → repo pick
                                                                        └─ deploys → owner → draft
Deploy and owner lookups are skipped after LOOKUP_CUTOFF_SECS (the turn deadline is 300 s)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

import drafting
import picker
import prompts
import render
import steps
from analysis import Analysis
from checks import matching_repos
from deps import TurnDeps
from discovery import DiscoveryFailure, discover
from inputs import MAX_REPOS
from records import UNKNOWN_OWNER, Findings

log = logging.getLogger("spend_spike.lookups")

UNTAGGED_NOTE = ("Deploy correlation not checked: the increase is mostly untagged, so there is no team to search "
                 "repositories for. Name them with \"repos: name\" to check.")


LOOKUP_FAILED_NOTE = "A {what} lookup failed ({error}), so its results are missing."


async def _guarded(what: str, coro, empty) -> steps.Lookup:
    """A lookup that raises (gateway down, model call failed) becomes a note; the analysis is never lost."""
    try:
        return await coro
    except Exception as e:  # every failure here degrades to a coverage note
        log.exception("%s lookup failed", what)
        return steps.Lookup(empty, LOOKUP_FAILED_NOTE.format(what=what, error=type(e).__name__))


@dataclass(frozen=True)
class Looked:
    findings: Findings
    pause: picker.Pause | None = None


def _with_notes(f: Findings, *notes: str | None) -> Findings:
    return replace(f, notes=tuple(dict.fromkeys((*f.notes, *(n for n in notes if n)))))


async def _reader(deps: TurnDeps) -> steps.Reader | str:
    try:
        catalog = await discover(deps, "read")
    except DiscoveryFailure as e:
        return str(e)
    return steps.Reader(deps, catalog)


async def run(deps: TurnDeps, a: Analysis, named_repos: Sequence[str]) -> Looked:
    """The full read side for a fresh analysis with a spike."""
    reader = await _reader(deps)
    if isinstance(reader, str):
        return Looked(await _drafted(deps, a, _with_notes(Findings(), reader)))
    ch = a.chain
    repos = tuple(named_repos)
    findings = Findings()
    if not repos:
        terms = [t for t in (ch.team if ch else None, ch.service_tag if ch else None) if t]
        if not terms:
            findings = _with_notes(findings, UNTAGGED_NOTE)
        else:
            found = await _guarded("repository", steps.find_repos(reader, terms), ())
            findings = _with_notes(findings, found.note)
            repos = matching_repos(found.value, terms, MAX_REPOS)
            if not repos and found.value:
                return Looked(findings, picker.repo_question(found.value, terms))
            if not repos and found.ran:
                findings = _with_notes(findings, f"No repository name matched {', '.join(terms)}, so deploy "
                                                 "correlation was not checked. Name one with \"repos: name\".")
    return Looked(await _rest(deps, reader, a, repos, findings))


async def with_repos(deps: TurnDeps, a: Analysis, repos: Sequence[str], prior: Findings) -> Findings:
    """Deploys, owner and draft for repositories the user picked or named after the first analysis."""
    reader = await _reader(deps)
    base = replace(prior, repos=(), changes=(), code_checked=False, draft="", notes=())
    if isinstance(reader, str):
        return await _drafted(deps, a, _with_notes(replace(base, owner=prior.owner, channels=prior.channels), reader))
    return await _rest(deps, reader, a, tuple(repos), base)


async def _rest(deps: TurnDeps, reader: steps.Reader, a: Analysis, repos: tuple[str, ...],
                findings: Findings) -> Findings:
    spike, ch = a.spike, a.chain
    assert spike is not None and ch is not None
    findings = replace(findings, repos=repos)
    if repos and deps.out_of_time():
        return await _drafted(deps, a, _with_notes(findings, prompts.TIME_LIMIT_NOTE))
    if repos:
        changes = await _guarded("deploy", steps.find_changes(reader, repos, spike.onset.day), ())
        findings = _with_notes(replace(findings, changes=changes.value, code_checked=changes.ran), changes.note)
    logins = tuple(dict.fromkeys(c.change.author_login for c in render.candidates(a, findings)
                                 if c.change.author_login))
    if ch.team or logins:
        if deps.out_of_time():
            return await _drafted(deps, a, _with_notes(findings, prompts.TIME_LIMIT_NOTE))
        owner = await _guarded("owner", steps.find_owner(reader, ch.team, logins), (UNKNOWN_OWNER, ()))
        found, channels = owner.value
        findings = _with_notes(replace(findings, owner=found, channels=channels), owner.note)
    return await _drafted(deps, a, findings)


async def _drafted(deps: TurnDeps, a: Analysis, findings: Findings) -> Findings:
    body = await drafting.draft(deps, render.facts(a, findings))
    log.info("lookups done: repos=%d changes=%d owner=%s", len(findings.repos), len(findings.changes),
             findings.owner.source)
    return replace(findings, draft=body)
