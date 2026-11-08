"""The code-host read: the organization's members, owners, outside collaborators and teams; repository
collaborators with their permissions (named repositories, up to 10, or the organization's first 30);
and the public profiles of members the roster cannot map (up to 40). Each step is a bounded tool loop;
code reads the facts out of the ledger. Anything that could not be read becomes a coverage line, and
the review goes on with the pasted data."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import prompts
import records
from deps import TurnDeps
from discovery import Discoverer
from identity import Roster
from reader import StepRun, TurnReader
from records import MAX_REPOS, OrgData

MAX_PROFILES = 40
MEMBER_CALLS, ADMIN_CALLS, PROFILE_CALLS = 2, 2, 2   # LLM calls per step loop: at most 6 a turn
STEP_SECS = 120.0


@dataclass(frozen=True)
class OrgScan:
    data: OrgData | None
    notes: tuple[str, ...] = ()


def fill(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


async def scan(deps: TurnDeps, org: str, repos: Sequence[str], roster: Roster, mappings: Mapping[str, str]) -> OrgScan:
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure is not None:
        return OrgScan(None, (f"Code-host org {org} not reviewed: {failure}",))
    reader = TurnReader(deps, discoverer)
    which = (f"Check only these repositories: {', '.join(repos)}." if repos else
             f"List the organization's repositories, then check the first {MAX_REPOS}.")
    runs = await asyncio.gather(
        reader.run("org_members", fill(prompts.ORG_MEMBERS_SYSTEM, org=org), "Start.", MEMBER_CALLS, STEP_SECS),
        reader.run("repo_admins", fill(prompts.REPO_ADMINS_SYSTEM, org=org, which=which, cap=str(MAX_REPOS)),
                   "Start.", ADMIN_CALLS, STEP_SECS))
    members, owners, outside, teams = records.org_members(reader.ledger.of("org_members"))
    admins, checked, listed, capped = records.repo_permissions(reader.ledger.of("repo_admins"))
    data = OrgData(org, members, owners, outside, teams, listed, admins, checked, capped)
    notes = [r.note for r in runs if r.note]
    if not data.read_anything:
        reason = "; ".join(notes) or "no member or repository data came back from the code-host tools."
        return OrgScan(None, (f"Code-host org {org} not reviewed: {reason}",))
    data, profile_notes = await _profiles(reader, data, roster, mappings)
    return OrgScan(data, tuple(dict.fromkeys((*notes, *profile_notes, *_cap_notes(data, repos)))))


async def _profiles(reader: TurnReader, data: OrgData, roster: Roster,
                    mappings: Mapping[str, str]) -> tuple[OrgData, tuple[str, ...]]:
    if not roster.present:
        return data, ()
    wanted = [m for m in data.members if not roster.by_handle(m) and m not in mappings]
    if not wanted:
        return data, ()
    asked, over = wanted[:MAX_PROFILES], len(wanted) - MAX_PROFILES
    run: StepRun = await reader.run("profiles", fill(prompts.PROFILES_SYSTEM, logins=", ".join(asked)), "Start.",
                                    PROFILE_CALLS, STEP_SECS)
    found = records.profiles(reader.ledger.of("profiles"))
    notes = tuple(n for n in (run.note, f"{over} member profiles were not fetched (cap {MAX_PROFILES})."
                              if over > 0 else "") if n)
    return replace(data, profiles={k: v for k, v in found.items() if k in asked}), notes


def _cap_notes(data: OrgData, repos: Sequence[str]) -> tuple[str, ...]:
    out = []
    if data.repos_capped:
        out.append(f"{data.repos_capped} repositories beyond the cap of {MAX_REPOS} were not checked for admins.")
    if not data.repos_checked:
        out.append("No repository permissions could be read, so repository admins were not reviewed.")
    missing = [r for r in repos if r.casefold() not in data.repos_checked]
    if repos and missing:
        out.append("Not checked: " + ", ".join(missing) + ".")
    return tuple(out)
