"""Turn 1's data gathering: (owner → repositories) → per repository batch, the manifests and lockfiles of
the advisory's ecosystem (root, first-level subfolders, packages/*, apps/*, services/*) → parse and match
in code. The model only drives the tools; every path, version and default branch is read by code out of
the turn's ledger. Pasted manifests take the same parse-and-match path."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import lockfiles
import manifests
import match
import prompts
import records
from advisory import Advisory
from deps import TurnDeps
from discovery import Discoverer
from fence import Call
from match import Exposure
from reader import TurnReader

log = logging.getLogger("dependency_risk.scan")

PASTED = "pasted"
MAX_OWNER_REPOS = 20
MAX_SCAN_REPOS = 8
BATCH = 4
REPOS_CALLS, REPOS_SECS = 2, 60.0
FIND_CALLS, FIND_SECS = 5, 180.0
_MANIFEST_NAMES = {"pip": "requirements*.txt, pyproject.toml", "npm": "package.json", "go": "go.mod",
                   "cargo": "Cargo.toml", "maven": "pom.xml, build.gradle, build.gradle.kts", "gem": "Gemfile"}
_LOCK_NAMES = {"pip": "poetry.lock", "npm": "package-lock.json", "cargo": "Cargo.lock"}
_ALL_MANIFESTS = "requirements*.txt, pyproject.toml, package.json, go.mod, Cargo.toml, pom.xml, build.gradle, Gemfile"


@dataclass(frozen=True)
class Scan:
    advisory: Advisory
    repos: tuple[str, ...]                       # repositories scanned, in order
    rows: tuple[Exposure, ...]                   # every declaration found, all statuses
    not_declared: tuple[str, ...] = ()           # scanned, manifests read, package not declared
    bases: dict = field(default_factory=dict)    # repo → default branch (tool output only)
    notes: tuple[str, ...] = ()                  # coverage lines
    cut: tuple[str, ...] = ()                    # repositories beyond the cap
    pasted: bool = False

    def to_dict(self) -> dict:
        return {"advisory": self.advisory.to_dict(), "repos": list(self.repos), "rows": [r.to_dict() for r in self.rows],
                "not_declared": list(self.not_declared), "bases": dict(self.bases), "notes": list(self.notes),
                "cut": list(self.cut), "pasted": self.pasted}

    @staticmethod
    def from_dict(data: dict) -> Scan:
        return Scan(advisory_of(data.get("advisory") or {}), tuple(data.get("repos") or ()),
                    tuple(Exposure.from_dict(r) for r in data.get("rows") or ()), tuple(data.get("not_declared") or ()),
                    dict(data.get("bases") or {}), tuple(data.get("notes") or ()), tuple(data.get("cut") or ()),
                    bool(data.get("pasted")))


def advisory_of(data: dict) -> Advisory:
    """An Advisory back from its `to_dict` form (state round-trip)."""
    names = set(Advisory.__dataclass_fields__)
    values = {k: (tuple(v) if k in ("affected", "fixed") else v) for k, v in dict(data).items() if k in names}
    return Advisory(**values)


def from_paste(advisory: Advisory, files: Sequence[tuple[str, str]]) -> Scan:
    found = [records.FileContent(path, "", text) for path, text in files]
    rows, declared = _assess(PASTED, found, advisory, set())
    unreadable = ("None of the pasted blocks is a manifest I can read (requirements*.txt, pyproject.toml, "
                  "package.json, go.mod, Cargo.toml, pom.xml, build.gradle, Gemfile).")
    notes = () if any(manifests.is_manifest(f.path) for f in found) else (unreadable,)
    return Scan(advisory, (PASTED,), tuple(rows), () if declared else (PASTED,), {}, notes, (), True)


async def scan(deps: TurnDeps, advisory: Advisory, repos: Sequence[str], owner: str,
               cut: Sequence[str] = ()) -> Scan | str:
    """The scan, or the reply that ends the turn."""
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return failure
    reader = TurnReader(deps, discoverer)
    notes: list[str] = []
    bases: dict[str, str] = {}
    names = list(repos)
    if owner and not names:
        listed, extra = await _owner_repos(reader, owner)
        if isinstance(listed, str):
            return listed
        everything = [r.full_name for r in listed]
        names, cut, notes = everything[:MAX_SCAN_REPOS], everything[MAX_SCAN_REPOS:], [*notes, *extra]
        bases.update({r.full_name: r.default_branch for r in listed if r.default_branch})
        if not names:
            return f"I found no repositories of `{owner}` that I can read."
    batches = [names[i:i + BATCH] for i in range(0, len(names), BATCH)]
    eco = advisory.ecosystem
    steps = await asyncio.gather(*(
        reader.run("find", prompts.find_system("\n".join(f"- {r}" for r in batch), _MANIFEST_NAMES.get(eco, _ALL_MANIFESTS),
                                               _LOCK_NAMES.get(eco, "their lockfiles")),
                   "Read them.", FIND_CALLS, FIND_SECS, tag=f"find{i}") for i, batch in enumerate(batches)))
    if all(s.status == "no_tools" for s in steps):
        return prompts.NO_CODE_TOOL
    notes += list(dict.fromkeys(s.note for s in steps if s.note))
    calls = reader.ledger.of(*(f"find{i}" for i in range(len(batches))))
    known = {n.casefold() for n in names}
    if not any(records.repo_of(c.arguments).casefold() in known for c in calls):
        return prompts.NO_REPO_DATA
    rows, not_declared, more = _per_repo(names, calls, advisory, bases)
    return Scan(advisory, tuple(names), tuple(rows), tuple(not_declared), bases, (*notes, *more), tuple(cut))


async def _owner_repos(reader: TurnReader, owner: str):
    step = await reader.run("repos", prompts.repos_system(owner, MAX_OWNER_REPOS), "List them.", REPOS_CALLS, REPOS_SECS)
    if step.status in ("no_tools", "paused"):
        return (prompts.NO_CODE_TOOL if step.status == "no_tools" else step.note), []
    found = [r for c in reader.ledger.of("repos") for r in records.repos(c.text)
             if r.full_name.casefold().startswith(owner.casefold() + "/") and not r.archived]
    unique = list({r.full_name.casefold(): r for r in found}.values())[:MAX_OWNER_REPOS]
    return unique, [n for n in (step.note,) if n]


def _per_repo(names: Sequence[str], calls: Iterable[Call], advisory: Advisory, bases: dict):
    by_repo: dict[str, list[Call]] = {}
    known = {n.casefold(): n for n in names}
    for call in calls:
        repo = known.get(records.repo_of(call.arguments).casefold())
        if repo:
            by_repo.setdefault(repo, []).append(call)
    rows: list[Exposure] = []
    not_declared, notes = [], []
    for repo in names:
        own = by_repo.get(repo, [])
        base = next((b for c in own if (b := records.default_branch(c.text))), "")
        if base:
            bases[repo] = base
        found = _files(own)
        alerts = {p for c in own for p in records.alert_packages(c.text)}
        repo_rows, declared = _assess(repo, found, advisory, alerts)
        rows += repo_rows
        notes += _coverage(repo, own, found, advisory)
        if not declared and any(manifests.is_manifest(f.path, advisory.ecosystem or None) for f in found):
            not_declared.append(repo)
    return rows, not_declared, notes


def _files(calls: Iterable[Call]) -> list[records.FileContent]:
    by_path: dict[str, records.FileContent] = {}
    for call in calls:
        for f in records.files(call.text):
            by_path.setdefault(f.path, f)
    return list(by_path.values())


def _assess(repo: str, found: Sequence[records.FileContent], advisory: Advisory, alerts: set[str]):
    rows: list[Exposure] = []
    declared = False
    texts = {f.path: f.text for f in found}
    for f in found:
        eco = manifests.ecosystem_of(f.path)
        if eco is None or (advisory.ecosystem and eco != advisory.ecosystem):
            continue
        deps_found = manifests.find(f.path, f.text, advisory.package, eco)
        declared = declared or bool(deps_found)
        for dep in deps_found:
            resolved = _resolved(f.path, texts, advisory.package, eco)
            rows.append(match.assess(dep, resolved, advisory, repo, f.url or None,
                                     alert=advisory.package.casefold() in alerts))
    return rows, declared


def _resolved(manifest_path: str, texts: dict[str, str], package: str, eco: str) -> tuple[str, ...]:
    folder = manifest_path.rsplit("/", 1)[0] + "/" if "/" in manifest_path else ""
    for name in lockfiles.lockfile_for(manifest_path):
        text = texts.get(folder + name)
        if text:
            found = lockfiles.resolved(folder + name, text, package, eco)
            if found:
                return found
    return ()


def _coverage(repo: str, calls: Sequence[Call], found: Sequence[records.FileContent], advisory: Advisory) -> list[str]:
    if not calls:
        return [f"{repo}: not read (no tool result for it this turn)."]
    eco = advisory.ecosystem or None
    read = {f.path for f in found}
    listed = {p for c in calls for p in records.listed_paths(c.text)}
    missed = sorted(p for p in listed if manifests.is_manifest(p, eco) and p not in read)
    notes = [f"{repo}: manifest {p} listed but not read." for p in missed[:5]]
    if not any(manifests.is_manifest(p, eco) for p in read) and not missed:
        notes.append(f"{repo}: no {advisory.ecosystem or 'supported'} manifest found.")
    return notes
