"""The code-host side of a turn: search → files (hit files + CODEOWNERS) → owners (commit history for paths
CODEOWNERS does not cover). The model only drives the tools; code reads every fact out of the raw output."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

import affected
import codeowners
import prompts
import records
from affected import PASTED, Affected
from deps import SEARCH_DEADLINE_SECS, TurnDeps
from discovery import Discoverer
from plan_model import Change
from reader import TurnReader
from search_plan import Needle, describe

log = logging.getLogger("terraform_blast_radius.steps")

SEARCH_CALLS = 3
FILES_CALLS, FILES_SECS = 2, 60.0
OWNERS_CALLS, OWNERS_SECS = 2, 45.0
MAX_FILES = 10
MAX_OWNER_PATHS = 6


@dataclass(frozen=True)
class Dependents:
    affected: tuple[Affected, ...] = ()
    notes: tuple[str, ...] = ()
    searched: bool = False      # the code host was searched at all
    complete: bool = False      # every step ran to the end with no tool failure
    codeowners: bool = False    # a CODEOWNERS file was read for every repository with dependents
    dropped: int = 0            # rows dropped by the verbatim check


async def find(deps: TurnDeps, needles: Sequence[Needle], changes: Mapping[str, Change],
               levels: Mapping[str, str], pasted: Sequence[records.Hit] = (), search: bool = True) -> Dependents:
    """`search=False` (no repository named): only pasted search output is classified, nothing is called."""
    discoverer = Discoverer(deps)
    failure = await discoverer.start() if needles and search else None
    if not needles or not search or failure:
        rows = affected.assemble(affected.attribute(pasted, needles, changes, {}), levels, {})
        notes = (failure,) if failure else ()
        return Dependents(tuple(rows), notes, searched=False, complete=False)
    reader = TurnReader(deps, discoverer)
    runs = [await reader.run("search", prompts.search_system(describe(needles)), SEARCH_CALLS,
                             SEARCH_DEADLINE_SECS)]
    if runs[0].status in ("no_tools", "deadline", "budget", "paused"):
        rows = affected.assemble(affected.attribute(pasted, needles, changes, {}), levels, {})
        return Dependents(tuple(rows), (runs[0].note,), searched=False, complete=False)
    hits = [h for c in reader.ledger.of("search") for h in records.code_hits(c.text, c.arguments)]
    wanted = _files_wanted(hits)
    if wanted:
        runs.append(await reader.run("files", prompts.files_system(_numbered(wanted)), FILES_CALLS, FILES_SECS))
    contents = _contents(reader, hits)
    found = affected.attribute([*hits, *pasted], needles, changes, contents)
    rules = _rules(contents)
    owners = {key: " ".join(codeowners.owners_for(rules[key[0]], key[1])) for _, h, _, _ in found
              if (key := (h.repo, h.path)) and h.repo in rules}
    owners = {k: v for k, v in owners.items() if v}
    missing = list(dict.fromkeys((h.repo, h.path) for _, h, _, _ in found
                                 if (h.repo, h.path) not in owners and h.repo != PASTED))[:MAX_OWNER_PATHS]
    if missing:
        since = (deps.now - timedelta(days=codeowners.COMMIT_WINDOW_DAYS)).date().isoformat()
        runs.append(await reader.run("owners", prompts.owners_system(_numbered(missing), since), OWNERS_CALLS,
                                     OWNERS_SECS))
        owners = {**owners, **_committers(reader, missing, deps)}
    rows = affected.assemble(found, levels, owners)
    kept, dropped = _verbatim(rows, reader, contents, pasted)
    repos_with_rows = {r.repo for r in kept if r.repo != PASTED}
    notes = tuple(dict.fromkeys([*(r.note for r in runs if r.note),
                                 *(f"no CODEOWNERS file read in {repo}: owners there come from recent commits"
                                   for repo in sorted(repos_with_rows - set(rules)))]))
    return Dependents(tuple(kept), notes, searched=True,
                      complete=all(r.status == "completed" and not r.note for r in runs),
                      codeowners=bool(repos_with_rows) and repos_with_rows <= set(rules), dropped=dropped)


def _files_wanted(hits: Sequence[records.Hit]) -> list[tuple[str, str]]:
    files = list(dict.fromkeys((h.repo, h.path) for h in hits))[:MAX_FILES]
    repos = list(dict.fromkeys(repo for repo, _ in files))
    return [*files, *((repo, path) for repo in repos for path in codeowners.CODEOWNERS_PATHS)]


def _numbered(items: Sequence[tuple[str, str]]) -> str:
    lines = []
    for i, (repo, path) in enumerate(items, start=1):
        owner, _, name = repo.partition("/")
        lines.append(f"{i}. owner {owner}, repo {name}, path {path}")
    return "\n".join(lines)


def _contents(reader: TurnReader, hits: Sequence[records.Hit]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for call in reader.ledger.of("files", "owners"):
        for ft in records.file_texts(call.text, call.arguments):
            repos = [ft.repo] if ft.repo else list(dict.fromkeys(h.repo for h in hits if h.path == ft.path))
            for repo in repos:
                out.setdefault((repo, ft.path), ft.text)
    return out


def _rules(contents: Mapping[tuple[str, str], str]) -> dict[str, tuple]:
    rules: dict[str, tuple] = {}
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):  # the code host's own lookup order
        for (repo, file_path), text in contents.items():
            if file_path == path and repo not in rules:
                parsed = codeowners.parse(text)
                if parsed:
                    rules[repo] = parsed
    return rules


def _committers(reader: TurnReader, missing: Sequence[tuple[str, str]], deps: TurnDeps) -> dict:
    by_path: dict[tuple[str, str], list[records.Commit]] = {}
    for call in reader.ledger.of("owners"):
        repo = records.repo_of(call.arguments)
        for commit in records.commits(call.text, call.arguments):
            key = next((k for k in missing if k[1] == commit.path and (not repo or k[0] == repo)), None)
            if key is not None:
                by_path.setdefault(key, []).append(commit)
    out = {}
    for key, found in by_path.items():
        top = codeowners.top_committer(found, deps.now)
        if top:
            out[key] = top if top.startswith("@") or " " in top else f"@{top}"
    return out


def _verbatim(rows: Sequence[Affected], reader: TurnReader, contents: Mapping, pasted: Sequence[records.Hit]
              ) -> tuple[list[Affected], int]:
    """Drop any row whose snippet or path is not in this turn's tool output or pasted text."""
    seen = "\n".join([*(c.text for c in reader.ledger.calls), *contents.values(),
                      *(f for h in pasted for f in h.fragments), *(h.path for h in pasted)])
    kept = [r for r in rows if (not r.snippet or r.snippet in seen) and r.path in seen]
    if len(kept) < len(rows):
        log.warning("dropped %d rows that failed the verbatim check", len(rows) - len(kept))
    return kept, len(rows) - len(kept)

