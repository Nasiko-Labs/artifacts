"""The read side of one guide, step by step (PLAN.md "Turn flow"):

    listing → files → ticket → keywords (LLM) → locate → prs → kb

Each step is a bounded tool loop (steps.py); code reads what came back (records.py) and decides the
next step's targets (plan.py). A step that cannot run (source missing, out of time) adds a coverage
line and the guide goes on without it. Fetching stops starting new steps after 180 s."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import timedelta

import codeowners
import coverage
import plan
import prompts
import records
import steps
from deps import TurnDeps
from intake import Request
from reader import BudgetExceeded, Reader, ask_once
from verify import parse_object

log = logging.getLogger("onboarding.gather")

FETCH_DEADLINE_SECS = 180
MAX_KEYWORDS = 8
MAX_KB_DOCS = 5


@dataclass(frozen=True)
class Gathered:
    repo: records.RepoInfo | None = None
    listing: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)          # path -> records.FileText
    issue: records.Issue | None = None
    keywords: tuple[str, ...] = ()
    hits: dict = field(default_factory=dict)           # keyword -> paths
    prs: tuple[records.PullRequest, ...] = ()
    kb: tuple[records.Doc, ...] = ()
    coverage: tuple[str, ...] = ()
    partial: bool = False
    listed: bool = False


async def collect(deps: TurnDeps, reader: Reader, req: Request) -> Gathered:
    got = await _listing(deps, reader, req)
    if not got.listed:
        return got
    got = await _files(deps, reader, req, got)
    got = await _ticket(deps, reader, req, got)
    got = await _keywords(deps, reader, got)
    got = await _locate(deps, reader, req, got)
    got = await _prs(deps, reader, req, got)
    return await _kb(deps, reader, req, got)


def _note(got: Gathered, line: str) -> Gathered:
    return replace(got, coverage=(*got.coverage, line))


def _with(got: Gathered, **changes) -> Gathered:
    return replace(got, **changes)


def _late(deps: TurnDeps) -> bool:
    return deps.elapsed() > FETCH_DEADLINE_SECS


async def _listing(deps: TurnDeps, reader: Reader, req: Request) -> Gathered:
    result = await steps.run(deps, reader, "listing", prompts.listing_system(req.repo))
    if not result.ok:
        return _note(Gathered(), coverage.line("code", result.note or prompts.NOT_READABLE))
    listing = records.entries(result.calls)
    return Gathered(repo=records.repo_info(result.calls), listing=listing, listed=bool(listing),
                    partial=result.partial)


async def _files(deps: TurnDeps, reader: Reader, req: Request, got: Gathered) -> Gathered:
    wanted = plan.key_files(got.listing)
    if not wanted:
        return _note(got, "no README, docs or build files found at the repository root")
    if _late(deps):
        return _note(got, f"files: {prompts.TIME_SKIPPED}")
    result = await steps.run(deps, reader, "files", prompts.files_system(req.repo, wanted))
    files = records.files(result.calls)
    missing = [p for p in wanted if p not in files]
    got = _with(got, files=files, partial=got.partial or result.partial)
    return _note(got, "not read: " + ", ".join(f"`{p}`" for p in missing)) if missing else got


async def _ticket(deps: TurnDeps, reader: Reader, req: Request, got: Gathered) -> Gathered:
    if req.ticket_text:
        return _with(got, issue=records.Issue("pasted ticket", req.ticket_text.splitlines()[0][:120], req.ticket_text))
    if not req.ticket:
        return _note(got, "no ticket given, so no first-ticket section")
    on_code_host = req.ticket.startswith("#") or req.ticket.startswith("http")
    if not reader.has_source("tracker") and not on_code_host:
        return _note(got, coverage.line("tracker", reader_reason(reader, "tracker")) +
                     f"; ticket {req.ticket} not read, paste its text to use it")
    if _late(deps):
        return _note(got, f"ticket: {prompts.TIME_SKIPPED}")
    ref = f"{req.ticket} in {req.repo}" if req.ticket.startswith("#") else req.ticket
    result = await steps.run(deps, reader, "ticket", prompts.ticket_system(ref), extra_source="code")
    found = records.issue(result.calls, req.ticket)
    if found is None:
        return _note(got, f"ticket {req.ticket}: not read ({result.note or 'not found'})")
    return _with(got, issue=found, partial=got.partial or result.partial)


def reader_reason(reader: Reader, source: str) -> str:
    return coverage.reason(reader.status(source))


async def _keywords(deps: TurnDeps, reader: Reader, got: Gathered) -> Gathered:
    if got.issue is None:
        return got
    text = f"{got.issue.title}\n{got.issue.body}"
    messages = ({"role": "system", "content": prompts.KEYWORDS_SYSTEM},
                {"role": "user", "content": prompts.fence(text)})
    try:
        answer = parse_object(await ask_once(deps, messages, reader.budget)) or {}
    except BudgetExceeded:
        return _note(got, "ticket keywords: skipped (call budget)")
    lowered = text.casefold()
    raw = answer.get("keywords") if isinstance(answer.get("keywords"), list) else []
    kept = tuple(dict.fromkeys(k.strip() for k in raw if isinstance(k, str) and len(k.strip()) > 2
                               and k.strip().casefold() in lowered))[:MAX_KEYWORDS]
    return _with(got, keywords=kept)


async def _locate(deps: TurnDeps, reader: Reader, req: Request, got: Gathered) -> Gathered:
    if not got.keywords:
        return got
    if _late(deps):
        return _note(got, f"locate: {prompts.TIME_SKIPPED}")
    result = await steps.run(deps, reader, "locate", prompts.locate_system(req.repo, got.keywords))
    hits: dict[str, tuple[str, ...]] = {}
    for call in result.calls:
        asked = json.dumps(dict(call.arguments), ensure_ascii=False).casefold()
        paths = records.search_hits(call, req.repo)
        for keyword in got.keywords:
            if keyword.casefold() in asked:
                hits = {**hits, keyword: tuple(dict.fromkeys((*hits.get(keyword, ()), *paths)))}
    return _with(got, hits=hits, partial=got.partial or result.partial)


async def _prs(deps: TurnDeps, reader: Reader, req: Request, got: Gathered) -> Gathered:
    if _late(deps):
        return _note(got, f"pull requests: {prompts.TIME_SKIPPED}")
    since = (deps.today - timedelta(days=plan.AUTHOR_WINDOW_DAYS)).isoformat()
    result = await steps.run(deps, reader, "prs", prompts.prs_system(req.repo, got.keywords, since))
    if not result.ok:
        return _note(got, f"pull requests: not read ({result.note or prompts.NOT_READABLE})")
    return _with(got, prs=records.pull_requests(result.calls), partial=got.partial or result.partial)


async def _kb(deps: TurnDeps, reader: Reader, req: Request, got: Gathered) -> Gathered:
    if not reader.has_source("docs"):
        return _note(got, coverage.line("docs", reader_reason(reader, "docs")) + "; only in-repo docs are listed")
    if _late(deps):
        return _note(got, f"doc store: {prompts.TIME_SKIPPED}")
    terms = (*got.keywords, req.repo.split("/")[-1])
    result = await steps.run(deps, reader, "kb", prompts.kb_system(terms))
    if not result.ok:
        return _note(got, coverage.line("docs", result.note or prompts.NOT_READABLE))
    words = [t.casefold() for t in terms]
    docs = sorted(records.documents(result.calls),
                  key=lambda d: (-sum(w in d.title.casefold() for w in words), d.title.casefold()))
    return _with(got, kb=tuple(docs[:MAX_KB_DOCS]))


def rules_of(got: Gathered) -> tuple[str, tuple[codeowners.Rule, ...]]:
    """(where CODEOWNERS was found, its rules)."""
    for path in codeowners.LOCATIONS:
        found = got.files.get(path)
        if found is not None:
            return path, codeowners.parse(found.text)
    return "", ()
