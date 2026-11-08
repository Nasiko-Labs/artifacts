"""Graph nodes, read side: intake (code), discover (tools), issue (read), candidates (code + tools).
Each node takes the checkpointed state and this turn's Scope and returns a state update."""

from __future__ import annotations

from dataclasses import dataclass, field

import candidates as cand
import followup as fu
import gather
import hitl
import prompts
import smalltalk
import steps
from checks import Dropped
from codehost import CodeHost
from discovery import Catalog, discover
from inputs import Incoming, PastedFile, RepoRef, Request, parse_request, repos_named
from llm import LlmError, TurnDeps
from shapes import IssueDoc
from terms import extract


@dataclass
class Scope:
    """One turn's inputs and live objects (never persisted)."""

    deps: TurnDeps
    incoming: Incoming
    resume_repo: RepoRef | None = None
    request: Request | None = None
    host: CodeHost | None = None
    catalog: Catalog | None = None
    dropped: Dropped = field(default_factory=Dropped)
    followup: fu.FollowUp | None = None


def _end(reply: str, **extra) -> dict:
    return {"reply": reply, "route": "", **extra}


def repo_dict(repo: RepoRef | None) -> dict | None:
    return None if repo is None else {"owner": repo.owner, "name": repo.name, "ref": repo.ref}


def repo_of(data: dict | None) -> RepoRef | None:
    return None if not data else RepoRef(data["owner"], data["name"], data.get("ref"))


async def intake(state: dict, scope: Scope) -> dict:
    text = scope.incoming.text
    if not scope.deps.traceparent:
        return _end(prompts.NO_TRACEPARENT_REPLY)
    follow = fu.classify(text) if state.get("envelope") else None
    if follow is not None and scope.resume_repo is None:
        scope.followup = follow
        return {"route": "followup"}
    request = parse_request(text)
    if scope.resume_repo is not None:
        request = Request(request.issue_text, scope.resume_repo, request.issue_number, request.files, request.hints)
    scope.request = request
    unanchored = request.repo is None and request.issue_number is None and not request.files \
        and not request.from_envelope
    if unanchored:
        try:
            in_scope = await steps.gate(scope.deps, text)
        except LlmError:
            in_scope = True
        if not in_scope:
            return _end(smalltalk.OFF_TOPIC_REPLY)
    if not request.has_issue:
        return _end(prompts.NEED_ISSUE)
    if request.repo is None and not request.files:
        options = repos_named(text) or tuple(filter(None, [_repo_full(state.get("repo"))]))
        pending = hitl.Pending(text, options, scope.incoming.wants_json)
        return _end("", pause=pending.to_metadata())
    return {"route": "discover", "hints": list(request.hints), "notes": []}


async def discover_node(state: dict, scope: Scope) -> dict:
    request = scope.request
    catalog, failure = await discover(scope.deps)
    if catalog is not None and catalog.has_code_host and request.repo is not None:
        scope.catalog = catalog
        scope.host = CodeHost(scope.deps, catalog)
        return {"route": "issue", "pasted_only": False}
    if request.files:
        return {"route": "issue", "pasted_only": True}
    return _end(failure or prompts.NO_SOURCE_REPLY)


async def issue_node(state: dict, scope: Scope) -> dict:
    request = scope.request
    pasted = request.issue_text
    fetched: IssueDoc | None = None
    if request.issue_number is not None and scope.host is not None and request.repo is not None:
        fetched = await gather.read_issue(scope.host, request.repo, request.issue_number)
    if fetched is None and not pasted:
        why = "" if scope.host is None or scope.host.has("issue") else " (no issue-reading tool is available)"
        repo = request.repo.full if request.repo else "the repository"
        return _end(prompts.ISSUE_UNREADABLE.format(number=request.issue_number, repo=repo, why=why))
    doc = _merge(fetched, pasted)
    number = request.issue_number or (state.get("issue_number") if scope.followup else None)
    return {"route": "candidates", "issue": _issue_dict(doc), "issue_number": number,
            "repo": repo_dict(request.repo)}


async def candidates_node(state: dict, scope: Scope) -> dict:
    request = scope.request
    issue = issue_doc(state["issue"])
    terms = extract(issue.title, issue.text())
    query = gather.query_tokens(issue.text(), request.hints)
    if state.get("pasted_only"):
        return _pasted(request.files, terms, query, request.hints)
    ref = await gather.read_ref(scope.host, request.repo)
    found = await gather.collect(scope.host, request.repo, ref, terms, query, request.hints)
    pasted_docs = {f.path: gather.visible(f.text).to_dict() for f in request.files}
    kept = dict(state.get("files") or {}) if scope.followup else {}
    listing = list(dict.fromkeys([*found.listing, *pasted_docs]))
    notes = list(state.get("notes") or [])
    if not found.candidates and found.listing:
        notes.append("no listed file matched the issue's words or a code search")
    if not found.listing and not pasted_docs:
        why = " (no repository listing tool is available)" if not scope.host.has("list") else \
            " (stopped at the tool-call or time budget)" if scope.host.coverage.budget_hit else ""
        return _end(prompts.NO_CANDIDATES.format(repo=request.repo.full, why=why))
    extra = cand.build(list(pasted_docs), {}, frozenset(pasted_docs), query) if pasted_docs else ()
    merged = list({c.path: c for c in (*extra, *found.candidates)}.values())
    return {"route": "pick_files", "ref": ref, "listing": listing, "files": {**kept, **pasted_docs},
            "candidates": [c.to_dict() for c in merged], "search_terms": list(terms.ranked[:10]), "notes": notes}


def _pasted(files: tuple[PastedFile, ...], terms, query, hints) -> dict:
    paths = [f.path for f in files]
    named = cand.mentioned(terms.paths, paths)
    built = cand.build(paths, {}, named, query, hints)
    kept = {c.path for c in built}
    extra = [cand.Candidate(p, 0.0, 0, 0, p in named) for p in paths if p not in kept and not cand.is_excluded(p)]
    return {"route": "pick_files", "ref": None, "listing": paths,
            "files": {f.path: gather.visible(f.text).to_dict() for f in files},
            "candidates": [c.to_dict() for c in (*built, *extra)], "search_terms": list(terms.ranked[:10])}


def _merge(fetched: IssueDoc | None, pasted: str) -> IssueDoc:
    if fetched is None:
        lines = pasted.strip().split("\n", 1)
        return IssueDoc(lines[0][:200], lines[1] if len(lines) > 1 else "")
    body = f"{fetched.body}\n\n{pasted}".strip() if pasted else fetched.body
    return IssueDoc(fetched.title, body, fetched.labels, fetched.comments, fetched.number)


def _issue_dict(doc: IssueDoc) -> dict:
    return {"title": doc.title, "body": doc.body, "labels": list(doc.labels), "comments": list(doc.comments),
            "number": doc.number}


def issue_doc(data: dict) -> IssueDoc:
    return IssueDoc(data.get("title") or "", data.get("body") or "", tuple(data.get("labels") or ()),
                    tuple(data.get("comments") or ()), data.get("number"))


def _repo_full(data: dict | None) -> str | None:
    repo = repo_of(data)
    return repo.full if repo else None
